"""QuantumOne CLI: interface do terminal para ferramentas locais e para a API do QuantumOne.

Adaptada da CLI independente (beta 0.2.2) para o backend atual:
- conversa pela análise unificada (POST /analise): base local primeiro, pesquisa na internet como
  complemento, fila de LLMs NVIDIA (um modelo de cada vez) e motor Qiskit, com o andamento ao vivo
  (GET /analise/progresso/{id});
- propostas de edição por POST /assistente/proposta, conferidas pelo Qiskit antes de gravar;
- otimização e diagnóstico pelo motor Qiskit (POST /qiskit/optimize e /qiskit/diagnose).
"""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from qone_core import (MANIFESTOS, Workspace, conferir_envio, desfazer, diagnosticar_circuito, diagnosticar_texto,
                       diff, e_circuito, estado_git, executar_testes, git_local, gravar, investigar, proposta,
                       ultima_edicao, verificar_circuito)
from qone_store import Registro

VERSAO = "0.3.0"
API_URL = os.getenv("QONE_API_URL", "http://localhost:8000")
# A análise pode percorrer a fila de LLMs inteira (até 30 s por modelo): mesmo teto do backend + folga.
TEMPO_ANALISE = float(os.getenv("QONE_ANALISE_TIMEOUT_SECONDS", "900")) + 30
AJUDA = """Projeto: /workspace [pasta], /clonar URL nome, /arquivos, /mapa, /buscar termo, /status
Contexto: /abrir arquivo, /anexar arquivo, /nova, /history, /retomar ID, /esquecer ID
Edição: /propor pedido, /criar caminho, /excluir caminho, /mover origem -> destino
Revisão: /diff, /aplicar, /cancelar, /desfazer
Verificação: /testar [unittest|pytest] [arquivo.py], /qiskit [0-3], /diagnosticar, /git [status|branches|diff|commit mensagem]
Conversa: /fontes, /model [perfil], /entradas, /eventos, /multi (encerre com .fim), /clear, /help, /sair
Sem comando, o texto vai para a análise do QuantumOne (base local + internet + LLMs + Qiskit)."""


def enviar(url, dados=None, timeout=120):
    corpo = None if dados is None else json.dumps(dados).encode("utf-8")
    pedido = Request(url, data=corpo, headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urlopen(pedido, timeout=timeout) as resposta:
        return json.load(resposta)


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFD",texto.casefold())
                   if not unicodedata.combining(c)).strip(" !?.")


class Painel:
    """Mostra a etapa e o modelo de LLM em uso enquanto a API trabalha (texto ao vivo)."""

    def __init__(self, rotulo):
        self.terminal = sys.stderr
        self.tty = self.terminal.isatty()
        self.ultima = None
        self.largura = 0
        self.escrever(rotulo)

    def escrever(self, texto):
        if self.tty:
            largura = max(20, shutil.get_terminal_size((100, 20)).columns - 1)
            linha = texto[:largura]
            print("\r" + linha.ljust(self.largura), end="", file=self.terminal, flush=True)
            self.largura = len(linha)

    def mostrar(self, estado):
        etapa = estado.get("etapa") or ""
        if self.tty:
            self.escrever(f"[{estado.get('segundos_total', 0):5.1f} s] {etapa}")
        elif etapa != self.ultima:
            print(f"· {etapa}", file=self.terminal, flush=True)
        self.ultima = etapa

    def limpar(self):
        if self.tty and self.largura:
            print("\r" + " " * self.largura + "\r", end="", file=self.terminal, flush=True)


class Terminal:
    def __init__(self, ws, args):
        self.ws=ws
        self.args=args
        self.api=args.api.rstrip("/")
        self.db=Registro(ws.raiz,self.api)
        self.resetar([])
        self.comandos=[]

    def resetar(self,selecao):
        self.nomes=list(selecao)
        self.sid=None
        self.cid=None
        self.assinatura=None
        self.titulo=""
        self.fontes=[]
        self.plano=""
        self.alteracoes={}
        self.diagnostico=""

    def aprovar(self,permissao,pergunta):
        print(pergunta,flush=True)
        if self.args.interativo:
            if input("Autorizar? [s/N] > ").strip().casefold() not in ("s","sim"):
                raise ValueError("Operação cancelada.")
        elif permissao not in self.args.permitir:
            raise ValueError(f"Permissão ausente: --permitir {permissao}")

    def postar(self,rota,dados):
        return enviar(self.api+rota,dados)

    def com_progresso(self, rota, dados, rotulo):
        """POST demorado (análise ou proposta) com o andamento ao vivo da fila de LLMs."""
        pid = str(uuid4())
        dados = {**dados, "progress_id": pid}
        resultado = {}

        def trabalhar():
            try:
                resultado["ok"] = enviar(self.api + rota, dados, timeout=TEMPO_ANALISE)
            except Exception as erro:  # repassado à thread principal
                resultado["erro"] = erro

        tarefa = threading.Thread(target=trabalhar, daemon=True)
        tarefa.start()
        painel = Painel(rotulo)
        try:
            while True:
                tarefa.join(0.6)
                if not tarefa.is_alive():
                    break
                try:
                    estado = enviar(f"{self.api}/analise/progresso/{pid}", timeout=5)
                except (HTTPError, URLError, OSError, ValueError):
                    continue
                painel.mostrar(estado)
        finally:
            painel.limpar()
        if "erro" in resultado:
            raise resultado["erro"]
        return resultado["ok"]

    def mostrar_fontes(self):
        if not self.fontes:
            print("A última resposta não tem fontes documentais.")
        contagem = {"local": 0, "web": 0}
        for f in self.fontes:
            origem = "web" if f.get("origin") == "web" else "local"
            contagem[origem] += 1
            rotulo = f"[Web {contagem['web']}]" if origem == "web" else f"[Fonte {contagem['local']}]"
            titulo = f.get("title") or f.get("chunk_id", "")
            print(f"{rotulo} {titulo}\n{f.get('source','')}\n{f.get('content','')}")

    def salvar(self,mensagem,resposta):
        self.titulo=self.titulo or mensagem
        self.sid=self.db.salvar_sessao(self.sid,self.nomes,self.titulo,self.cid,self.assinatura,resposta)

    def mapa(self):
        with atividade("Abrindo pastas..."):
            idx=self.ws.indice()
        print("Workspace:",self.ws.raiz)
        print("Manifestos:",", ".join(n for n in sorted(MANIFESTOS) if (self.ws.raiz/n).is_file()) or "nenhum")
        for n,item in idx.items():
            print(f"{n} | símbolos: {', '.join(item['simbolos']) or '—'} | imports: {', '.join(item['imports']) or '—'}",item.get("aviso",""))
        return idx

    def chat(self,pedido):
        """Pergunta à análise unificada. Os arquivos abertos vão junto quando mudaram desde a última
        mensagem; o histórico da conversa fica no backend (conversation_id)."""
        conferir_envio(pedido)
        arquivos, assinatura = [], None
        if self.nomes:
            conteudos={n:self.ws.ler(n).decode("utf-8-sig") for n in self.nomes}
            for conteudo in conteudos.values():
                conferir_envio(conteudo)
            assinatura=hashlib.sha256(json.dumps(conteudos,ensure_ascii=False).encode()).hexdigest()
            if self.assinatura!=assinatura:
                arquivos=[{"nome":n,"conteudo":c} for n,c in conteudos.items()]
        corpo={"mensagem":pedido,"arquivos":arquivos}
        if self.cid:
            corpo["conversation_id"]=self.cid
        resposta=self.com_progresso("/analise",corpo,"Pensando...")
        self.cid=resposta.get("conversation_id") or self.cid
        self.assinatura=assinatura
        self.fontes=resposta.get("sources",[])
        self.salvar(pedido,{"response":resposta["resposta"],"sources":self.fontes})
        print("QuantumOne >",resposta["resposta"])
        self.rodape(resposta)
        if self.args.fontes:
            self.mostrar_fontes()

    def rodape(self, resposta):
        llm = resposta.get("llm") or {}
        if resposta.get("answer_mode") == "llm" and llm.get("modelo"):
            print(f"[Redigida por {llm['modelo']}]")
        elif resposta.get("answer_mode") == "local":
            print("[Resposta extrativa local, sem LLM]")
        for c in resposta.get("circuitos", []):
            correcao = c.get("correcao") or {}
            estado = "válido" if c.get("valido_original") else f"{c.get('problemas', 0)} problema(s)"
            linha = f"Qiskit · {c['origem']}: {estado}"
            if correcao.get("metodo") not in (None, "nenhuma"):
                linha += f"; correção {correcao['metodo']} {'validada' if correcao.get('valido') else 'não validada'}"
            otim = c.get("otimizacao") or {}
            if otim:
                linha += (f"; portas {otim['original']['total_operacoes']}→{otim['proposta']['total_operacoes']}, "
                          f"equivalente={otim.get('equivalente')}")
            print(linha)
        for aviso in resposta.get("warnings", [])[:3]:
            print("!", aviso)
        if self.fontes:
            web = sum(1 for f in self.fontes if f.get("origin") == "web")
            print(f"Fontes: {len(self.fontes) - web} da base local, {web} da internet (/fontes para ver)")

    def sessao(self,acao,identificador):
        itens=self.db.sessoes()
        if acao=="/history":
            for r in itens:
                print(r["id"],r["titulo"],r["selecao"])
            if not itens:
                print("Nenhuma conversa salva neste workspace.")
            return
        encontrados=[r for r in itens if identificador and r["id"].startswith(identificador)]
        if len(encontrados)!=1:
            raise ValueError("Informe um ID único de /history.")
        r=encontrados[0]
        if acao=="/esquecer":
            self.aprovar("historico","Excluir a referência local a esta conversa?")
            self.db.esquecer(r["id"])
            if r["id"]==self.sid:
                self.resetar([])
            return
        selecao=json.loads(r["selecao"])
        for n in selecao:
            self.ws.caminho(n)
        self.resetar(selecao)
        self.sid,self.cid,self.assinatura,self.titulo=r["id"],r["conversa"],r["assinatura"],r["titulo"]
        ultima=json.loads(r["ultima"])
        self.fontes=ultima.get("sources",[])
        print("Conversa retomada:",self.titulo,"\nQuantumOne >",ultima.get("response",""))

    def planejar(self,pedido):
        self.alteracoes={}
        if not self.nomes:
            raise ValueError("Selecione a implementação e o teste com /abrir e /anexar.")
        if not pedido:
            raise ValueError("Use /propor seguido da alteração desejada.")
        plano,arquivos,fontes=proposta(self.ws,self.nomes,pedido,
            lambda dados:self.com_progresso("/assistente/proposta",dados,"Preparando proposta..."),self.diagnostico)
        self.plano,self.alteracoes,self.fontes=plano,arquivos,fontes
        self.revisar()
        self.conferir_qiskit()
        if self.args.fontes:
            self.mostrar_fontes()

    def conferir_qiskit(self):
        """Antes de /aplicar, o motor Qiskit confere os circuitos alterados (nada é executado)."""
        for nome,(_,depois) in self.alteracoes.items():
            if depois is None or not e_circuito(nome, depois):
                continue
            try:
                with atividade("Conferindo com o Qiskit..."):
                    d=diagnosticar_texto(nome,depois.decode("utf-8-sig"),self.postar)
            except (HTTPError,URLError,OSError,ValueError) as erro:
                print(f"Qiskit · {nome}: não foi possível conferir ({detalhar(erro)})")
                continue
            erros=[p for p in d.get("problemas",[]) if p.get("severidade")=="erro"]
            if d.get("validacao",{}).get("valido") and not erros:
                print(f"Qiskit · {nome}: a versão proposta carrega e não tem erros.")
            else:
                print(f"Qiskit · {nome}: {len(erros)} erro(s) na versão proposta.")
                for p in erros[:5]:
                    print(f"  linha {p.get('linha') or '-'}: [{p['codigo']}] {p['mensagem']}")

    def revisar(self):
        if not self.alteracoes:
            print("Nenhuma alteração pendente.")
            return
        print("Plano:",self.plano,"\nArquivos:",", ".join(self.alteracoes))
        print(diff(self.alteracoes))
        print("Use /aplicar para autorizar esta proposta ou /cancelar.")

    def operacao_arquivo(self,acao,argumento):
        self.alteracoes={}
        if not argumento:
            raise ValueError("Informe o caminho.")
        if acao=="/mover":
            caminhos=argumento.split(" -> ",1)
            if len(caminhos)!=2:
                raise ValueError("Use /mover origem.py -> destino.py")
        else:
            caminhos=[argumento]
        mudancas={}
        if acao in ("/excluir","/mover"):
            origem=self.ws.caminho(caminhos[0]).relative_to(self.ws.raiz).as_posix()
            original=self.ws.ler(origem)
            mudancas[origem]=(original,None)
        if acao in ("/criar","/mover"):
            dest=self.ws.caminho(caminhos[-1],novo=True)
            if dest.exists():
                raise ValueError("O destino já existe.")
            mudancas[dest.relative_to(self.ws.raiz).as_posix()]=(None,original if acao=="/mover" else b"")
        self.alteracoes=mudancas
        self.plano=f"{acao[1:]} {argumento}"
        self.revisar()

    def rodar_testes(self,argumento):
        runner,_,alvo=argumento.partition(" ")
        if runner not in ("pytest","unittest"):
            alvo,runner=argumento,"pytest"
        if alvo:
            alvo=self.ws.caminho(alvo.strip('"')).relative_to(self.ws.raiz).as_posix()
        self.aprovar("testes",f"Executar testes locais com {sys.executable} -m {runner} em {self.ws.raiz}? Alvo: {alvo or 'suíte'}. O código do projeto rodará com sua conta.")
        r=executar_testes(self.ws,runner,alvo,lambda s:print(s,flush=True),self.args.timeout_testes)
        resumo=f"{runner}: {r['estado']}, saída {r['codigo']}, duração {r['segundos']} s"
        print(resumo)
        self.db.evento("testes",resumo)
        self.diagnostico=r["saida"]
        if r["codigo"] or r["estado"]!="concluído":
            return r["codigo"] if r["codigo"]>0 else 1
        return 0

    def otimizar(self, argumento):
        if not self.nomes:
            raise ValueError("Abra o circuito primeiro.")
        if argumento and not argumento.isdigit():
            raise ValueError("Use /qiskit seguido de um nível de 0 a 3.")
        if not self.nomes[0].lower().endswith(".qasm"):
            # Em Python, linhas inválidas seriam deixadas de fora da construção: corrija antes de otimizar.
            with atividade("Conferindo o código..."):
                d=diagnosticar_circuito(self.ws,self.nomes[0],self.postar)
            erros=[p for p in d.get("problemas",[]) if p.get("severidade")=="erro"]
            if erros:
                print(f"{self.nomes[0]} tem {len(erros)} erro(s) que impedem a otimização:")
                for p in erros[:5]:
                    print(f"  linha {p.get('linha') or '-'}: [{p['codigo']}] {p['mensagem']}")
                print("Use /diagnosticar para ver e aplicar a correção validada, depois /qiskit de novo.")
                return
        with atividade("Analisando circuito..."):
            r=verificar_circuito(self.ws,self.nomes[0],self.postar,int(argumento or 3))
        print("Arquivo:",self.nomes[0])
        if r.get("perfil"):
            print("Perfil:",r["perfil"],"| estratégia:",r.get("estrategia_aplicada") or "—")
        for campo in ("total_operacoes","profundidade","operacoes_dois_qubits"):
            if campo in r["original"]:
                print(campo,r["original"][campo],"→",r["proposta"][campo])
        ao,af=r.get("analise_original") or {},r.get("analise_final") or {}
        if ao.get("cx_equivalentes") is not None:
            print("cx_equivalentes",ao["cx_equivalentes"],"→",af.get("cx_equivalentes"))
        v=r.get("verificacao") or {}
        print("Equivalência:",r["status_verificacao"],r.get("motivo") or "",
              f"({v['metodo']}, garantia {v.get('garantia')})" if v.get("metodo") else "")
        sim=r.get("simulacao") or {}
        if sim.get("metodo"):
            print("Simulação recomendada:",sim["metodo"])
        for aviso in r.get("warnings",[])[:3]:
            print("!",aviso)
        if self.args.fontes and r.get("circuito_otimizado_qasm"):
            print(r["circuito_otimizado_qasm"])
        self.db.evento("qiskit",f"{self.nomes[0]}: {r['status_verificacao']}")

    def diagnosticar(self):
        """Todos os problemas do arquivo aberto; a correção validada vira uma proposta para /aplicar."""
        if not self.nomes:
            raise ValueError("Abra o arquivo primeiro.")
        nome=self.nomes[0]
        with atividade("Diagnosticando..."):
            d=diagnosticar_circuito(self.ws,nome,self.postar)
        resumo=d.get("resumo") or {}
        print(f"Arquivo: {nome} | linguagem: {d.get('linguagem') or 'não identificada'} | "
              f"erros: {resumo.get('erros',0)} | avisos: {resumo.get('avisos',0)}")
        erros=[]
        for p in d.get("problemas",[]):
            if p.get("severidade")=="erro":
                erros.append(p)
            descricao=(p.get("correcao") or {}).get("descricao")
            print(f"[{p['severidade'].upper()}] linha {p.get('linha') or '-'}: [{p['codigo']}] {p['mensagem']}"
                  + (f"\n  Correção: {descricao}" if descricao else ""))
        corrigido=d.get("codigo_corrigido")
        if not erros:
            print("Nenhum erro encontrado pelo Qiskit.")
            return
        if not (corrigido and d.get("validacao",{}).get("valido")):
            print("A correção automática não pôde ser validada. Use /propor para pedir uma correção à LLM.")
            return
        original=self.ws.ler(nome)
        texto=corrigido.replace("\r\n","\n")
        if b"\r\n" in original:
            texto=texto.replace("\n","\r\n")
        novo=texto.encode("utf-8-sig" if original.startswith(b"\xef\xbb\xbf") else "utf-8")
        if novo==original:
            return
        self.alteracoes={nome:(original,novo)}
        self.plano="Correção automática do analisador do QuantumOne, validada pelo Qiskit."
        self.revisar()

    def status(self):
        try:
            saude=enviar(self.api+"/health",timeout=5)
            api=f"{self.api} ({saude.get('status','ok')})"
        except (HTTPError,URLError,OSError,ValueError) as erro:
            api=f"{self.api} (indisponível: {detalhar(erro)})"
        print("Workspace:",self.ws.raiz,"\nAPI:",api,"\nSeleção:",self.nomes,
              "\nPesquisa na internet: sempre ativa (a base local é consultada primeiro)",
              "\nLLMs: fila NVIDIA do backend, um modelo de cada vez",
              "\nSessão:",self.sid or "nova","\nConversa no backend:",self.cid or "nova",
              "\nProposta:",list(self.alteracoes))

    def comando(self,texto):
        partes=texto.strip().split(maxsplit=1)
        if not partes:
            return
        cmd=partes[0].casefold()
        arg=partes[1].strip() if len(partes)>1 else ""
        natural=normalizar(texto)
        if cmd=="/help":
            print(AJUDA)
        elif cmd=="/clear":
            print("\033[2J\033[H" if sys.stdout.isatty() else "")
        elif cmd=="/multi":
            if not self.args.interativo:
                raise ValueError("Use uma pergunta multilinha entre aspas no modo script.")
            print("Digite a mensagem; termine com .fim em uma linha.")
            linhas=[]
            while (l:=input())!=".fim":
                linhas.append(l)
            if linhas:
                self.chat("\n".join(linhas))
        elif cmd=="/workspace":
            if arg:
                with atividade("Abrindo pastas..."):
                    novo=Workspace(arg.strip('"'))
                self.aprovar("workspace",f"Trocar workspace para {novo.raiz}?")
                self.ws,self.db=novo,Registro(novo.raiz,self.api)
                self.resetar([])
                self.comandos=[]
            print(self.ws.raiz)
        elif cmd=="/entradas":
            for i,anterior in enumerate(self.comandos,1):
                print(i,anterior)
        elif cmd=="/clonar":
            partes=arg.split(maxsplit=1)
            if len(partes)!=2:
                raise ValueError("Use /clonar https://servidor/projeto.git nome-da-pasta")
            url,nome=partes
            endereco=urlsplit(url)
            if endereco.scheme!="https" or not endereco.hostname or endereco.username or endereco.password or endereco.query or endereco.fragment:
                raise ValueError("Use URL Git HTTPS sem credenciais.")
            if not re.fullmatch(r"[\w-]+",nome):
                raise ValueError("Informe um nome de pasta simples.")
            dest=self.ws.raiz/nome
            if dest.exists() or dest.is_symlink():
                raise ValueError("O destino já existe.")
            self.aprovar("clonar",f"Clonar {url} em {dest}?")
            print(git_local(self.ws,"-c","protocol.file.allow=never","-c","protocol.ext.allow=never",
                            "clone","--no-recurse-submodules","--",url,nome))
            self.db.evento("clone aprovado",nome)
            print("Clone concluído. Use /workspace",dest)
        elif cmd=="/status":
            self.status()
        elif cmd=="/model":
            perfis=json.loads(os.environ.get("QONE_PERFIS","{}"))
            if not isinstance(perfis,dict):
                raise ValueError("QONE_PERFIS deve ser um objeto JSON de nome para URL da API.")
            if arg:
                url=perfis.get(arg)
                if not isinstance(url,str) or not url.startswith(("http://","https://")):
                    raise ValueError("Perfil inexistente. Configure QONE_PERFIS com nome e URL.")
                self.aprovar("workspace",f"Enviar próximas consultas ao serviço do perfil {arg}: {url}?")
                self.api=url.rstrip("/")
                self.db=Registro(self.ws.raiz,self.api)
                self.resetar(self.nomes)
            print("API ativa:",self.api,"| perfis:",", ".join(perfis) or "padrão",
                  "| modelos: fila NVIDIA configurada no backend (NVIDIA_MODELS)")
        elif cmd=="/arquivos":
            with atividade("Abrindo pastas..."):
                lista = "\n".join(self.ws.arquivos())
            print(lista or "Nenhum .py ou .qasm encontrado.")
        elif cmd=="/mapa" or natural in ("analise este repositorio","analise o repositorio","mapeie o projeto"):
            self.mapa()
        elif cmd=="/buscar":
            if not arg:
                raise ValueError("Use /buscar seguido do símbolo ou texto.")
            for nome in self.ws.arquivos():
                p=self.ws.caminho(nome)
                if p.stat().st_size<=512000:
                    for i,l in enumerate(p.read_text("utf-8-sig",errors="replace").splitlines(),1):
                        if arg.casefold() in l.casefold():
                            print(f"{nome}:{i}: {l.strip()}")
        elif cmd in ("/abrir","/anexar"):
            p=self.ws.caminho(arg.strip('"'))
            with atividade("Abrindo arquivo..."):
                self.ws.ler(p)
            nome=p.relative_to(self.ws.raiz).as_posix()
            if cmd=="/abrir":
                self.resetar([nome])
            elif nome not in self.nomes:
                self.nomes.append(nome)
                self.alteracoes={}
            print("Arquivo aberto:" if cmd=="/abrir" else "Anexado:",nome)
        elif cmd=="/nova":
            self.resetar(self.nomes)
            print("Nova conversa.")
        elif cmd in ("/history","/retomar","/esquecer"):
            self.sessao(cmd,arg)
        elif cmd=="/fontes":
            self.mostrar_fontes()
        elif cmd=="/git":
            status=estado_git(self.ws)
            if not arg or arg=="status":
                print(status)
            elif arg=="branches":
                print(git_local(self.ws,"branch","--list"))
            elif arg=="diff":
                print(git_local(self.ws,"diff","--no-ext-diff","--no-textconv"))
                print(git_local(self.ws,"diff","--cached","--no-ext-diff","--no-textconv"))
            elif arg.startswith("commit "):
                mensagem=arg[7:].strip()
                preparados=git_local(self.ws,"diff","--cached","--name-status")
                if not preparados or not mensagem:
                    raise ValueError("Prepare os arquivos no Git e informe a mensagem.")
                self.aprovar("commit",f"Criar commit local com a mensagem {mensagem!r}?\n{preparados}")
                print(git_local(self.ws,"commit","-m",mensagem))
                self.db.evento("commit aprovado",git_local(self.ws,"rev-parse","--short","HEAD"))
            else:
                raise ValueError("Use /git status, branches, diff ou commit mensagem.")
        elif cmd=="/eventos":
            for linha in self.db.historico_eventos():
                print(*linha)
        elif cmd=="/propor" or re.search(r"\bcorrija (?:os testes|o teste)\b",natural) or natural.startswith("refatore "):
            if not self.nomes and cmd!="/propor":
                with atividade("Abrindo pastas..."):
                    self.nomes=investigar(self.ws,self.diagnostico)
                print("Contexto selecionado:",", ".join(self.nomes))
            self.planejar(arg if cmd=="/propor" else texto)
        elif cmd in ("/criar","/mover","/excluir"):
            self.operacao_arquivo(cmd,arg)
        elif cmd=="/diff":
            self.revisar()
        elif cmd=="/cancelar":
            self.alteracoes={}
            print("Proposta descartada.")
        elif cmd=="/aplicar":
            if arg:
                raise ValueError("Use /aplicar sozinho após revisar o diff.")
            if not self.alteracoes:
                print("Nenhuma proposta pendente.")
                return
            if not self.args.interativo and "editar" not in self.args.permitir:
                raise ValueError("Use --permitir editar para aplicar em modo script.")
            nomes=list(self.alteracoes)
            with atividade("Alterando arquivos..."):
                gravar(self.ws,self.db,self.plano,self.alteracoes)
            self.resetar([n for n in self.nomes if (self.ws.raiz/n).is_file()])
            print("Alterações salvas:",", ".join(nomes))
            self.db.evento("aplicação aprovada",", ".join(nomes))
        elif cmd=="/desfazer":
            e=ultima_edicao(self.ws,self.db)
            arquivos=list(json.loads(e["arquivos"]))
            self.aprovar("editar",f"Reverter a edição {e['id']} em {', '.join(arquivos)}?")
            with atividade("Restaurando arquivos..."):
                desfazer(self.ws,self.db,e["id"])
            self.resetar([n for n in self.nomes if (self.ws.raiz/n).is_file()])
            self.db.evento("reversão aprovada",", ".join(arquivos))
            print("Versão anterior restaurada.")
        elif cmd=="/testar" or natural in ("rode os testes","execute os testes"):
            return self.rodar_testes(arg if cmd=="/testar" else "")
        elif cmd=="/qiskit":
            self.otimizar(arg)
        elif cmd=="/diagnosticar":
            self.diagnosticar()
        elif cmd.startswith("/"):
            raise ValueError("Comando desconhecido. Use /help.")
        elif natural in ("oi","ola","opa","bom dia","boa tarde","boa noite"):
            self.fontes=[]
            print("QuantumOne > Olá! O que você quer analisar?")
        else:
            self.chat(texto)


@contextmanager
def atividade(mensagem):
    terminal = sys.stderr
    visivel = terminal.isatty() and sys.stdout.isatty()
    if visivel:
        print(mensagem, end="", file=terminal, flush=True)
    try:
        yield
    finally:
        if visivel:
            print("\r" + " " * len(mensagem) + "\r", end="", file=terminal, flush=True)


def mostrar_abertura():
    terminal = sys.stderr
    colorido = terminal.isatty() and "NO_COLOR" not in os.environ and os.getenv("TERM") != "dumb"
    roxo = "\033[38;5;141m" if colorido else ""
    fim = "\033[0m" if colorido else ""
    linhas = ("q₀ ──[H]──●──", "          │", "q₁ ───────⊕──")
    try:
        "".join(linhas).encode(terminal.encoding or "utf-8")
    except UnicodeEncodeError:
        linhas = ("q0 --[H]--*--", "          |", "q1 -------+--")
    for linha, legenda in zip(linhas, ("QuantumOne", f"CLI · {VERSAO}", "")):
        print(f"  {roxo}{linha:13}{fim}   {legenda}".rstrip(), file=terminal)


def main():
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="replace")  # console Windows sem UTF-8 não derruba a CLI
    p=argparse.ArgumentParser(description="QuantumOne · assistente para projetos Qiskit")
    p.add_argument("arquivo",nargs="?",type=Path)
    p.add_argument("pergunta",nargs="?")
    p.add_argument("--repo",type=Path)
    p.add_argument("--chat",action="store_true")
    p.add_argument("--api",default=API_URL)
    p.add_argument("--fontes",action="store_true")
    p.add_argument("--roteiro",type=Path,help="Um comando por linha; use --permitir para efeitos locais.")
    p.add_argument("--permitir",action="append",default=[],choices=["testes","editar","workspace","historico","commit","clonar"])
    p.add_argument("--timeout-testes",type=int,default=120)
    p.add_argument("--version",action="version",version=f"QuantumOne CLI {VERSAO}")
    args=p.parse_args()
    args.interativo=not args.roteiro and (args.chat or not (args.arquivo or args.pergunta))
    if args.timeout_testes<1:
        p.error("--timeout-testes precisa ser positivo.")
    try:
        raiz=args.repo or (args.arquivo.absolute().parent if args.arquivo else Path.cwd())
        ws=Workspace(raiz)
        if not args.repo and not args.arquivo:
            if not args.interativo or input(f"Autorizar workspace {ws.raiz}? [s/N] > ").strip().casefold() not in ("s","sim"):
                p.exit(1,"Workspace não autorizado. Informe --repo no modo script.\n")
        term=Terminal(ws,args)
        if args.interativo:
            mostrar_abertura()
        if args.arquivo:
            caminho=args.arquivo if args.repo else args.arquivo.absolute()
            term.comando("/abrir "+str(caminho))
        if args.roteiro:
            mensagens=args.roteiro.read_text("utf-8-sig").splitlines()
        else:
            mensagens=[]
        if args.pergunta:
            mensagens.insert(0,args.pergunta)
        if not args.interativo and not mensagens:
            p.error("Informe pergunta, --chat ou --roteiro.")
        for texto in mensagens:
            if texto.strip().casefold()=="/sair":
                return
            try:
                status=term.comando(texto)
            except (OSError,ValueError,SyntaxError,KeyError,sqlite3.Error,subprocess.SubprocessError) as erro:
                print(detalhar(erro),file=sys.stderr)
                if not args.interativo:
                    return 1
                continue
            if status and not args.interativo:
                return status
        while args.interativo:
            try:
                texto=input("\nVocê > ").strip()
                if texto.casefold()=="/sair":
                    return
                if texto and texto!="/entradas":
                    term.comandos.append(texto)
                term.comando(texto)
            except EOFError:
                return
            except KeyboardInterrupt:
                print("\nInterrompido.")
            except (OSError,ValueError,SyntaxError,KeyError,sqlite3.Error,subprocess.SubprocessError) as erro:
                print(detalhar(erro),file=sys.stderr)
    except (OSError,ValueError,SyntaxError,KeyError,sqlite3.Error,subprocess.SubprocessError) as erro:
        print(detalhar(erro),file=sys.stderr)
        return 1
    except (KeyboardInterrupt,EOFError):
        return 130


def detalhar(erro):
    """Mensagem legível para erros da API (formato {code, message} do backend) e de rede."""
    if isinstance(erro,HTTPError):
        corpo=erro.read().decode("utf-8",errors="replace")
        try:
            dados=json.loads(corpo)
            mensagem=dados.get("message") or (dados.get("error") or {}).get("message") or corpo
            codigo=dados.get("code") or (dados.get("error") or {}).get("code") or ""
            return " ".join(f"Erro HTTP {erro.code} {codigo}: {mensagem}".split())
        except (ValueError,AttributeError):
            return f"Erro HTTP {erro.code}: {corpo}"
    if isinstance(erro,URLError):
        return f"API indisponível ({erro.reason}). Inicie o backend com: docker compose up -d --wait api"
    return str(erro)


if __name__=="__main__":
    sys.exit(main())
