"""Ferramentas locais da CLI do QuantumOne. Nenhuma operação depende de input() ou print().

Adaptada ao backend atual: propostas por POST /assistente/proposta (fila de LLMs NVIDIA),
otimização e diagnóstico pelo motor Qiskit (POST /qiskit/optimize e /qiskit/diagnose).
"""
import ast
import codecs
import difflib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from qone_store import empacotar, desempacotar

IGNORAR = {".git", ".venv", "venv", "__pycache__", "node_modules", ".next", "dist",
           "build", ".pytest_cache", ".mypy_cache", ".tox", ".quantumone"}
EXTENSOES = {".py", ".qasm"}
MANIFESTOS = {"pyproject.toml", "package.json", "requirements.txt", "compose.yaml", "pytest.ini"}


class Workspace:
    def __init__(self, pasta):
        self.raiz = Path(pasta).resolve(strict=True)
        self._indice = {}
        if not self.raiz.is_dir():
            raise ValueError("Escolha uma pasta.")

    def caminho(self, nome, *, novo=False):
        relativo = Path(nome)
        if relativo.is_absolute():
            try:
                relativo = relativo.relative_to(self.raiz)
            except ValueError:
                raise ValueError("Arquivo fora do workspace.") from None
        if ".." in relativo.parts or any(p.startswith(".") or p.casefold() in IGNORAR
                                          for p in relativo.parts):
            raise ValueError("Caminho fora do escopo autorizado.")
        caminho = self.raiz / relativo
        if caminho.suffix.lower() not in EXTENSOES or caminho.resolve() != caminho:
            raise ValueError("Escolha um .py ou .qasm regular dentro do workspace.")
        if not (novo and not caminho.exists()) and not caminho.is_file():
            raise ValueError(f"Arquivo não encontrado: {relativo}")
        return caminho

    def arquivos(self):
        for pasta, dirs, nomes in os.walk(self.raiz):
            base = Path(pasta)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and
                             d.casefold() not in IGNORAR and (base/d).resolve() == base/d)
            for nome in sorted(nomes):
                p = base / nome
                if not nome.startswith(".") and p.suffix.lower() in EXTENSOES and not p.is_symlink() and p.is_file():
                    yield p.relative_to(self.raiz).as_posix()

    def indice(self):
        itens = {}
        for nome in self.arquivos():
            dados = {"imports": [], "simbolos": []}
            if nome.endswith(".py"):
                p = self.caminho(nome)
                dados_stat = p.stat()
                assinatura = (dados_stat.st_size, dados_stat.st_mtime_ns, dados_stat.st_ctime_ns)
                anterior = self._indice.get(nome)
                if anterior and anterior[0] == assinatura:
                    itens[nome] = anterior[1]
                    continue
                if dados_stat.st_size > 512_000:
                    dados["aviso"] = "grande demais para índice"
                else:
                    try:
                        arvore = ast.parse(p.read_text(encoding="utf-8-sig"))
                        for n in ast.walk(arvore):
                            if isinstance(n, ast.Import):
                                dados["imports"].extend(i.name for i in n.names)
                            elif isinstance(n, ast.ImportFrom):
                                prefixo = "." * n.level + (n.module or "")
                                dados["imports"].append(prefixo)
                                for alias in n.names:
                                    separador = "." if n.module else ""
                                    dados["imports"].append(prefixo + separador + alias.name)
                            elif isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                                dados["simbolos"].append(n.name)
                    except (SyntaxError, UnicodeError, OSError) as e:
                        dados["aviso"] = str(e)
                self._indice[nome] = assinatura, dados
            itens[nome] = dados
        for removido in self._indice.keys() - itens.keys():
            del self._indice[removido]
        return itens

    def ler(self, nome):
        p = self.caminho(nome)
        if p.stat().st_size > 512_000:
            raise ValueError(f"Arquivo muito grande para análise: {nome}")
        return p.read_bytes()


SEGREDOS = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{25,}|AIza[0-9A-Za-z_-]{30,}|nvapi-[A-Za-z0-9_-]{20,})|"
    r"""(?im:^\s*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|PASSWORD|SECRET_KEY)\s*=\s*["'][^"']{8,}["'])"""
)


def conferir_envio(texto):
    if SEGREDOS.search(texto):
        raise ValueError("Possível credencial no conteúdo selecionado. Remova-a antes do envio.")


def diff(alteracoes):
    linhas = []
    for nome, (antes, depois) in alteracoes.items():
        a = (antes or b"").decode("utf-8-sig").splitlines(keepends=True)
        b = (depois or b"").decode("utf-8-sig").splitlines(keepends=True)
        trecho = list(difflib.unified_diff(a, b, nome if antes is not None else "/dev/null",
                                           nome if depois is not None else "/dev/null", lineterm=""))
        linhas.extend(l.rstrip("\r\n") for l in trecho)
        if not trecho:
            linhas.append(f"{nome}: mudança de arquivo vazio ou codificação")
    return "\n".join(linhas)


def _sem_repeticao_das_seguintes(novas, seguintes):
    """Inserção que termina com cópia exata das linhas que já vêm depois dela é erro de intervalo do
    modelo (ele queria substituir, mas usou fim=inicio-1): a cópia é descartada para não duplicar código."""
    seguintes = [l.rstrip("\r\n") for l in seguintes[:len(novas)]]
    for k in range(min(len(novas) - 1, len(seguintes)), 0, -1):
        if novas[-k:] == seguintes[:k] and any(l.strip() for l in seguintes[:k]):
            return novas[:-k]
    return novas


def editar_linhas(nome, original, mudancas):
    texto = original.decode("utf-8-sig")
    linhas = texto.splitlines(keepends=True)
    saida, cursor = [], 0
    if not isinstance(mudancas, list):
        raise ValueError("Lista de alterações inválida.")
    for item in mudancas:
        if not isinstance(item, dict):
            raise ValueError("Alteração inválida.")
        inicio, fim, novas = item.get("inicio"), item.get("fim"), item.get("linhas")
        if type(inicio) is not int or type(fim) is not int or not (cursor < inicio <= len(linhas) + 1 and inicio - 1 <= fim <= len(linhas)):
            raise ValueError("Intervalo fora do original ou sobreposto.")
        if not isinstance(novas, list) or not all(isinstance(l,str) and "\r" not in l and "\n" not in l for l in novas):
            raise ValueError("Substituição inválida.")
        if fim == inicio - 1:
            novas = _sem_repeticao_das_seguintes(novas, linhas[inicio-1:])
        saida.extend(linhas[cursor:inicio-1])
        ultima = linhas[fim-1] if fim >= inicio else "\r\n" if "\r\n" in texto else "\n"
        final = ultima[len(ultima.rstrip("\r\n")):]
        if novas:
            if fim < inicio and saida and not saida[-1].endswith(("\n", "\r")):
                saida.append(final)
            saida.append((final or ("\r\n" if "\r\n" in texto else "\n")).join(novas) + final)
        cursor = fim
    novo = "".join(saida + linhas[cursor:])
    if nome.lower().endswith(".py"):
        compile(novo, nome, "exec")
    return novo.encode("utf-8-sig" if original.startswith(codecs.BOM_UTF8) else "utf-8")


def proposta(ws, nomes, pedido, consultar, diagnostico=""):
    originais = {n: ws.ler(n) for n in nomes}
    for original in originais.values():
        conferir_envio(original.decode("utf-8-sig"))
    numerados = {n: "\n".join(f"{i}: {l}" for i,l in enumerate(b.decode("utf-8-sig").splitlines(),1))
                 for n,b in originais.items()}
    # As instruções de formato ficam no backend (POST /assistente/proposta); aqui vão só os dados.
    dados = {"pedido": pedido, "arquivos": numerados, "resultado_testes": diagnostico[-8000:]}
    serializado = json.dumps(dados, ensure_ascii=False)
    if len(serializado) > 60000:
        raise ValueError("O contexto ultrapassa 60.000 caracteres. Reduza a seleção.")
    conferir_envio(serializado)
    resposta = consultar(dados)
    texto = (resposta.get("response") or "").strip()
    obj = _json_da_resposta(texto)
    if not isinstance(obj,dict) or not isinstance(obj.get("arquivos"),dict):
        raise ValueError("A API não retornou uma proposta estruturada.")
    for campo in ("plano","riscos","verificacao"):
        if not isinstance(obj.get(campo),str) or not obj[campo].strip():
            raise ValueError(f"Proposta sem {campo}.")
    alteracoes = {}
    for nome, edicoes in obj["arquivos"].items():
        if nome not in originais:
            raise ValueError("A proposta inclui arquivo fora da seleção.")
        novo = editar_linhas(nome,originais[nome],edicoes)
        if novo != originais[nome]:
            alteracoes[nome] = (originais[nome],novo)
    plano = f"{obj['plano']}\nRiscos: {obj['riscos']}\nVerificação: {obj['verificacao']}"
    return plano, alteracoes, resposta.get("sources",[])


def _json_da_resposta(texto):
    """Aceita o JSON puro ou dentro de um bloco ```json (alguns modelos ainda cercam a resposta)."""
    bloco = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", texto, re.S | re.I)
    candidato = bloco[1] if bloco else texto
    try:
        return json.loads(candidato)
    except json.JSONDecodeError:
        inicio, fim = candidato.find("{"), candidato.rfind("}")
        if inicio < 0 or fim <= inicio:
            raise ValueError("A API não retornou uma proposta estruturada.") from None
        try:
            return json.loads(candidato[inicio:fim + 1])
        except json.JSONDecodeError:
            raise ValueError("A API não retornou uma proposta estruturada.") from None


def substituir(ws, nome, esperado, novo, modo):
    p = ws.caminho(nome, novo=True)
    atual = p.read_bytes() if p.exists() else None
    if atual != esperado:
        raise ValueError(f"{nome} mudou no disco. Revise o arquivo antes de continuar.")
    if novo is None:
        p.unlink()
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(dir=p.parent, delete=False) as f:
            temporario = Path(f.name)
            f.write(novo)
            f.flush()
            os.fsync(f.fileno())
        temporario.chmod(modo)
        if ws.caminho(nome,novo=True) != p or (p.read_bytes() if p.exists() else None) != esperado:
            raise ValueError(f"{nome} mudou durante a gravação.")
        os.replace(temporario,p)
    finally:
        if temporario:
            temporario.unlink(missing_ok=True)


def gravar(ws, registro, plano, alteracoes):
    entradas = {}
    with registro.transacao() as db:
        if db.execute("SELECT 1 FROM edicao WHERE raiz=? AND estado='pendente'",(str(ws.raiz),)).fetchone():
            raise ValueError("Há uma gravação interrompida. Use /desfazer para recuperar.")
        for nome,(antes,depois) in alteracoes.items():
            p=ws.caminho(nome,novo=True)
            if (p.read_bytes() if p.exists() else None)!=antes:
                raise ValueError(f"{nome} mudou no disco. Gere uma nova proposta.")
            modo=stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600
            entradas[nome]=[empacotar(antes),empacotar(depois),modo]
        eid=db.execute("INSERT INTO edicao(raiz,plano,arquivos,estado) VALUES(?,?,?,'pendente')",
                       (registro.raiz,plano,json.dumps(entradas))).lastrowid
    feitos=[]
    with registro.transacao() as db:
        if db.execute("SELECT estado FROM edicao WHERE id=?",(eid,)).fetchone()[0]!="pendente":
            raise ValueError("Edição interrompida por outra sessão.")
        try:
            for nome,(antes,depois) in alteracoes.items():
                substituir(ws,nome,antes,depois,entradas[nome][2])
                feitos.append(nome)
        except Exception as erro:
            for nome in reversed(feitos):
                antes,depois=alteracoes[nome]
                substituir(ws,nome,depois,antes,entradas[nome][2])
            db.execute("UPDATE edicao SET estado='abortada' WHERE id=?",(eid,))
            db.commit()
            raise erro
        db.execute("UPDATE edicao SET estado='aplicada' WHERE id=?",(eid,))


def ultima_edicao(ws,registro):
    with registro.transacao() as db:
        r=db.execute("SELECT * FROM edicao WHERE raiz=? AND estado IN ('aplicada','pendente') ORDER BY id DESC LIMIT 1",
                     (str(ws.raiz),)).fetchone()
    if not r:
        raise ValueError("Não há edição do agente para desfazer.")
    return r


def desfazer(ws,registro,eid):
    with registro.transacao() as db:
        r=db.execute("SELECT * FROM edicao WHERE raiz=? AND estado IN ('aplicada','pendente') ORDER BY id DESC LIMIT 1",
                     (str(ws.raiz),)).fetchone()
        if not r or r["id"]!=eid:
            raise ValueError("Histórico mudou. Confira novamente.")
        restaurar=[]
        for nome,(a,b,modo) in json.loads(r["arquivos"]).items():
            antes,depois=desempacotar(a),desempacotar(b)
            p=ws.caminho(nome,novo=True)
            atual=p.read_bytes() if p.exists() else None
            if atual==antes and r["estado"]=="pendente":
                continue
            if atual!=depois:
                raise ValueError(f"{nome} mudou depois da edição. Reversão interrompida.")
            restaurar.append((nome,depois,antes,modo))
        db.execute("UPDATE edicao SET estado='pendente' WHERE id=?",(eid,))
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT estado FROM edicao WHERE id=?",(eid,)).fetchone()[0]!="pendente":
            raise ValueError("Edição já recuperada por outra sessão.")
        for args in reversed(restaurar):
            substituir(ws,*args)
        db.execute("UPDATE edicao SET estado='desfeita' WHERE id=?",(eid,))


def executar_testes(ws, runner, alvo, progresso, limite=120):
    """Executa somente a suíte escolhida; o chamador já obteve aprovação."""
    import threading
    from collections import deque
    if runner not in ("pytest","unittest"):
        raise ValueError("Escolha unittest ou pytest.")
    if alvo:
        arquivo=ws.caminho(alvo)
        if arquivo.suffix.lower()!=".py":
            raise ValueError("Informe um teste Python.")
        alvo=arquivo.relative_to(ws.raiz).as_posix()
    argv=[sys.executable,"-m",runner]
    if runner=="pytest":
        argv+=["-q"]+([alvo] if alvo else [])
    elif alvo:
        argv.append(alvo)
    else:
        argv+=["discover","-s","tests" if (ws.raiz/"tests").is_dir() else "."]
    env={k:v for k,v in os.environ.items() if k.upper() in {
        "PATH","HOME","TMP","TEMP","TMPDIR","SYSTEMROOT","WINDIR","USERPROFILE","LOCALAPPDATA",
        "LANG","LC_ALL","VIRTUAL_ENV","COMSPEC","PATHEXT"}}
    env.update(PYTHONIOENCODING="utf-8",PYTHONUNBUFFERED="1",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    inicio=time.monotonic()
    proc=subprocess.Popen(argv,cwd=ws.raiz,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,start_new_session=os.name!="nt")
    ultimas=deque(maxlen=64)
    def ler():
        with proc.stdout as pipe:
            while trecho:=pipe.readline(4096):
                linha=trecho.decode("utf-8","replace").rstrip()
                if SEGREDOS.search(linha):
                    linha = "[linha com possível credencial omitida]"
                ultimas.append(linha)
                progresso(linha)
    leitor=threading.Thread(target=ler,daemon=True)
    leitor.start()
    estado="concluído"
    try:
        proc.wait(timeout=limite)
    except (subprocess.TimeoutExpired,KeyboardInterrupt) as erro:
        estado="cancelado" if isinstance(erro,KeyboardInterrupt) else "tempo excedido"
        if os.name=="nt":
            subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],capture_output=True)
        else:
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()
    leitor.join(timeout=1)
    return {"estado":estado,"codigo":proc.returncode,"segundos":round(time.monotonic()-inicio,2),
            "saida":"\n".join(list(ultimas))[-6000:]}


# Mesmo perfil que a interface usa: o mais forte que segue exigindo verificação de equivalência.
PERFIL_OTIMIZACAO = "avancado"


def _corpo_circuito(nome, texto):
    return {"qasm": texto} if nome.lower().endswith(".qasm") else {"code": texto, "format": "python"}


def verificar_circuito(ws,nome,postar,nivel=3):
    if nivel not in range(4):
        raise ValueError("Nível Qiskit deve estar entre 0 e 3.")
    texto=ws.ler(nome).decode("utf-8-sig")
    conferir_envio(texto)
    corpo={**_corpo_circuito(nome,texto),"optimization_level":nivel,"perfil":PERFIL_OTIMIZACAO}
    if not nome.lower().endswith(".qasm"):
        # Arquivo de projeto: só a construção do circuito é otimizada (imports de simulador, print etc. ficam de fora).
        corpo["extrair_construcao"]=True
    return postar("/qiskit/optimize",corpo)


def diagnosticar_texto(nome, texto, postar):
    """Diagnóstico completo (todos os problemas, correção e validação). Nada é executado."""
    conferir_envio(texto)
    formato = "qasm" if nome.lower().endswith(".qasm") else "python"
    return postar("/qiskit/diagnose", {"code": texto, "format": formato, "politica": "conservadora"})


def diagnosticar_circuito(ws, nome, postar):
    return diagnosticar_texto(nome, ws.ler(nome).decode("utf-8-sig"), postar)


def e_circuito(nome, conteudo):
    """Arquivos que o motor Qiskit consegue conferir: .qasm e Python que usa Qiskit."""
    if nome.lower().endswith(".qasm"):
        return True
    texto = (conteudo or b"").decode("utf-8-sig", errors="replace")
    return nome.lower().endswith(".py") and ("QuantumCircuit" in texto or "qiskit" in texto)


def estado_git(ws):
    env=dict(os.environ,GIT_OPTIONAL_LOCKS="0",GIT_TERMINAL_PROMPT="0")
    def git(*args):
        r=subprocess.run(["git","--no-pager","-c","core.fsmonitor=false",*args],cwd=ws.raiz,
                         capture_output=True,text=True,errors="replace",env=env,timeout=15)
        if r.returncode:
            raise ValueError(r.stderr.strip() or "Git indisponível.")
        return r.stdout.strip()
    topo=Path(git("rev-parse","--show-toplevel")).resolve()
    if topo!=ws.raiz:
        raise ValueError("Para inspecionar o Git, abra a raiz do clone como workspace.")
    return git("status","--short","--branch")


def investigar(ws, diagnostico):
    """Seleciona testes e módulos locais conectados pelos imports para uma proposta."""
    indice = ws.indice()
    testes = [n for n in indice if Path(n).name.startswith("test_") or Path(n).name.endswith("_test.py")]
    falhas = [n for n in testes if n in diagnostico or Path(n).stem in diagnostico]
    alvos = falhas or testes
    if not alvos:
        raise ValueError("Nenhum módulo de teste identificado. Selecione arquivos com /abrir e /anexar.")
    modulos = {n[:-3].replace("/", ".").removesuffix(".__init__"): n for n in indice if n.endswith(".py")}
    selecao = list(dict.fromkeys(alvos))
    for nome in selecao:
        pacote = nome.split("/")[:-1]
        for ref in indice[nome]["imports"]:
            pontos = len(ref) - len(ref.lstrip("."))
            if pontos:
                base = pacote[:max(len(pacote) + 1 - pontos, 0)]
                ref = ".".join([*base, ref.lstrip(".")]).strip(".")
            modulo = modulos.get(ref) or modulos.get(f"src.{ref}")
            if modulo and modulo not in selecao:
                selecao.append(modulo)
    if sum(ws.caminho(n).stat().st_size for n in selecao) > 23000:
        raise ValueError("Seleção automática muito grande. Rode /testar em um teste específico ou use /abrir e /anexar.")
    return selecao


def git_local(ws, *args):
    # shell desativado; não interroga credenciais ao consultar estado.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    retorno = subprocess.run(["git", "--no-pager", "-c", "core.fsmonitor=false", *args],
                             cwd=ws.raiz, env=env, capture_output=True, text=True,
                             errors="replace", timeout=60)
    if retorno.returncode:
        raise ValueError(retorno.stderr.strip() or "Git não concluiu a operação.")
    return retorno.stdout.strip()
