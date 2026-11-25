"""QuantumOne CLI: usa o mesmo backend do Desktop Chat.

Exemplos:
    python qone_cli.py saude
    python qone_cli.py analisar "Corrija e otimize meu projeto" --anexo meu_projeto/
    python qone_cli.py analisar "Por que dá erro?" --codigo circuito.qasm --sem-web
    python qone_cli.py perguntar "Como medir todos os qubits?"
    python qone_cli.py diagnosticar circuito.qasm --politica expandir_registradores
    python qone_cli.py otimizar circuito.qasm --perfil avancado --topologia heavy_hex --portas ibm_heron
    cat programa.py | python qone_cli.py diagnosticar -

A URL da API vem de --api ou da variável QONE_API_URL (padrão http://127.0.0.1:8000).
Nenhum código é executado localmente; tudo é analisado pelo backend isolado.
"""
import argparse
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import httpx

API_PADRAO = os.getenv("QONE_API_URL", "http://127.0.0.1:8000")
TEMPO_ANALISE = float(os.getenv("QONE_ANALISE_TIMEOUT_SECONDS", "900")) + 30
# Mesmos limites de POST /analise (contratos_analise.py).
MAX_ARQUIVOS, MAX_BYTES_ARQUIVO, MAX_BYTES_TOTAL = 300, 300_000, 3_000_000
PASTAS_IGNORADAS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build",
                    ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints", ".idea", ".vscode"}
EXTENSOES_TEXTO = {".qasm", ".py", ".ipynb", ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml",
                   ".cfg", ".ini", ".csv", ".inc"}


def _ler(caminho):
    if caminho == "-":
        return sys.stdin.read()
    with open(caminho, encoding="utf-8") as arquivo:
        return arquivo.read()


def _coletar_anexos(caminhos, saida):
    """Lê arquivos e pastas (recursivamente) como [{nome, conteudo}], respeitando os limites da API."""
    arquivos, total = [], 0
    for caminho in caminhos:
        base = Path(caminho)
        if base.is_dir():
            candidatos = [(item, item.relative_to(base.parent).as_posix()) for item in sorted(base.rglob("*"))
                          if item.is_file() and item.suffix.lower() in EXTENSOES_TEXTO
                          and not PASTAS_IGNORADAS.intersection(item.relative_to(base).parts)]
        elif base.is_file():
            candidatos = [(base, base.name)]
        else:
            saida.write(f"! Anexo não encontrado: {caminho}\n")
            continue
        for item, nome in candidatos:
            try:
                conteudo = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                saida.write(f"! Ignorado (não é texto UTF-8): {nome}\n")
                continue
            tamanho = len(conteudo.encode("utf-8"))
            if tamanho > MAX_BYTES_ARQUIVO or total + tamanho > MAX_BYTES_TOTAL or len(arquivos) >= MAX_ARQUIVOS:
                saida.write(f"! Ignorado (limite de {MAX_ARQUIVOS} arquivos, 300 KB cada, 3 MB no total): {nome}\n")
                continue
            arquivos.append({"nome": nome[:500], "conteudo": conteudo})
            total += tamanho
    return arquivos


def _analisar(cliente, args, saida):
    """Envia POST /analise e, enquanto espera, mostra cada etapa e o modelo consultado no momento."""
    progress_id = str(uuid.uuid4())
    corpo = {"mensagem": args.mensagem or "", "formato": args.formato, "perfil": args.perfil, "top_k": args.top_k,
             "pesquisa_web": not args.sem_web, "progress_id": progress_id,
             "arquivos": _coletar_anexos(args.anexo or [], saida)}
    if args.codigo:
        corpo["codigo"] = _ler(args.codigo)
    if args.conversa:
        corpo["conversation_id"] = args.conversa
    resultado = {}

    def enviar():
        try:
            resultado["resposta"] = cliente.post("/analise", json=corpo, timeout=TEMPO_ANALISE)
        except httpx.HTTPError as erro:
            resultado["erro"] = erro

    tarefa = threading.Thread(target=enviar, daemon=True)
    tarefa.start()
    ultima = None
    while True:
        tarefa.join(1.0)
        if not tarefa.is_alive():
            break
        try:
            r = cliente.get(f"/analise/progresso/{progress_id}", timeout=5)
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            estado = r.json()
            if estado["etapa"] != ultima:
                ultima = estado["etapa"]
                saida.write(f"[{estado['segundos_total']:6.1f} s] {ultima}\n")
                saida.flush()
    if "erro" in resultado:
        raise resultado["erro"]
    return resultado["resposta"]


def _imprimir_analise(corpo, saida):
    saida.write("\n" + corpo["resposta"].rstrip() + "\n")
    llm = corpo.get("llm") or {}
    if corpo.get("answer_mode") == "llm" and llm.get("modelo"):
        saida.write(f"\n[Redigida por {llm['modelo']}]\n")
    else:
        saida.write({"local": "\n[extrativa local (sem LLM)]\n",
                     "none": "\n[sem resposta (evidência insuficiente)]\n"}.get(corpo.get("answer_mode"), ""))
    for t in llm.get("tentativas") or []:
        saida.write(f"  tentativa: {t.get('modelo')} -> {t.get('resultado')} ({t.get('segundos')} s)\n")
    for c in corpo.get("circuitos", []):
        corr = c.get("correcao") or {}
        estado = "válido" if c.get("valido_original") else f"inválido, {c.get('problemas', 0)} problema(s)"
        saida.write(f"\nCircuito {c['origem']} ({c.get('formato') or '?'}): {estado}\n")
        for linha in c.get("diagnostico_resumo", []):
            saida.write(f"  {linha}\n")
        if corr.get("metodo") != "nenhuma":
            quem = f" por {corr['modelo']}" if corr.get("modelo") else ""
            saida.write(f"  Correção {corr['metodo']}{quem}: {'validada pelo Qiskit' if corr.get('valido') else 'não validada'}\n")
            for falha in corr.get("falhas", []):
                saida.write(f"  ! {falha}\n")
            if corr.get("valido") and corr.get("codigo"):
                saida.write(corr["codigo"].rstrip() + "\n")
        o = c.get("otimizacao") or {}
        if o:
            a, b = o.get("original") or {}, o.get("proposta") or {}
            saida.write(f"  Otimização ({o.get('perfil')}): portas {a.get('total_operacoes')} -> {b.get('total_operacoes')}, "
                        f"profundidade {a.get('profundidade')} -> {b.get('profundidade')}, "
                        f"CX equivalentes {o.get('cx_equivalentes_antes')} -> {o.get('cx_equivalentes_depois')}, "
                        f"equivalente={o.get('equivalente')}\n")
        if c.get("erro"):
            saida.write(f"  ! {c['erro']}\n")
    if corpo.get("arquivos"):
        tipos = {}
        for a in corpo["arquivos"]:
            tipos[a["tipo"]] = tipos.get(a["tipo"], 0) + 1
        saida.write("\nArquivos: " + ", ".join(f"{n} {t}" for t, n in sorted(tipos.items())) + "\n")
    for aviso in corpo.get("warnings", []):
        saida.write(f"! {aviso}\n")
    locais = [f for f in corpo.get("sources", []) if f.get("origin", "local") == "local"]
    web = [f for f in corpo.get("sources", []) if f.get("origin") == "web"]
    if locais or web:
        saida.write(f"\nFontes consultadas ({len(locais) + len(web)}):\n")
    for rotulo, lista in (("Fonte", locais), ("Web", web)):
        for numero, fonte in enumerate(lista, start=1):
            saida.write(f"  [{rotulo} {numero}] {fonte.get('title') or fonte['document_id']} - {fonte['source']}\n")
    saida.write(f"\nTempo total: {corpo.get('tempo_total_segundos')} s\n")


def _erro(resposta):
    try:
        corpo = resposta.json()
    except ValueError:
        return f"HTTP {resposta.status_code}"
    detalhes = (corpo.get("error") or {}).get("details") or {}
    extra = f" (linha {detalhes['line']})" if detalhes.get("line") else ""
    return f"{corpo.get('code', resposta.status_code)}: {corpo.get('message', '')}{extra}"


def _imprimir_resposta(corpo, saida):
    saida.write(corpo["response"].rstrip() + "\n")
    modo = {"llm": "gerada por LLM com trechos locais", "local": "extrativa local (sem LLM)",
            "none": "sem resposta (evidência insuficiente)"}.get(corpo.get("answer_mode"), "")
    if modo:
        saida.write(f"\n[{modo}]\n")
    for aviso in corpo.get("warnings", []):
        saida.write(f"! {aviso}\n")
    if corpo.get("sources"):
        saida.write(f"\nFontes consultadas ({len(corpo['sources'])}):\n")
        for numero, fonte in enumerate(corpo["sources"], start=1):
            saida.write(f"  [{numero}] {fonte.get('title') or fonte['document_id']} - {fonte['source']}\n")


def _imprimir_diagnostico(corpo, saida):
    resumo = corpo["resumo"]
    saida.write(f"Linguagem: {corpo['linguagem'] or 'não identificada'} | erros: {resumo['erros']} | "
                f"avisos: {resumo['avisos']} | confiança: {corpo['confianca_global']}\n")
    for p in corpo["problemas"]:
        linha = f"linha {p['linha']}" if p["linha"] else "-"
        saida.write(f"\n[{p['severidade'].upper()}] {p['codigo']} ({linha}, confiança {p['confianca']})\n")
        saida.write(f"  {p['mensagem']}\n")
        if p.get("causa_raiz"):
            saida.write(f"  Causa: {p['causa_raiz']}\n")
        if p["correcao"].get("descricao"):
            saida.write(f"  Correção: {p['correcao']['descricao']}\n")
        for alternativa in p["alternativas"]:
            saida.write(f"  Alternativa: {alternativa}\n")
    if corpo.get("codigo_corrigido"):
        validado = corpo["validacao"].get("valido")
        saida.write(f"\nCódigo corrigido ({'validado' if validado else 'não validado'}):\n{corpo['codigo_corrigido']}")
    for limite in corpo.get("limitacoes", []):
        saida.write(f"\nLimitação: {limite}")
    saida.write("\n")


def _imprimir_otimizacao(corpo, saida):
    o, p = corpo["original"], corpo["proposta"]
    v = corpo.get("verificacao") or {}
    saida.write(f"Perfil: {corpo.get('perfil')} | estratégia: {corpo.get('estrategia_aplicada')}\n")
    saida.write(f"Qubits: {o['qubits']} | portas: {o['total_operacoes']} -> {p['total_operacoes']} | "
                f"profundidade: {o['profundidade']} -> {p['profundidade']} | "
                f"2 qubits: {o['operacoes_dois_qubits']} -> {p['operacoes_dois_qubits']}\n")
    ao, af = corpo.get("analise_original") or {}, corpo.get("analise_final") or {}
    if ao:
        saida.write(f"CX equivalentes: {ao.get('cx_equivalentes')} -> {af.get('cx_equivalentes')}\n")
    saida.write(f"Verificação: {v.get('metodo')} ({v.get('garantia')}) -> equivalente={corpo['equivalente']}\n")
    sim = corpo.get("simulacao") or {}
    if sim:
        saida.write(f"Simulação recomendada: {sim.get('metodo')} - {sim.get('justificativa')}\n")
    for c in corpo.get("compromissos", []):
        saida.write(f"Compromisso: {c}\n")
    for aviso in corpo.get("warnings", []):
        saida.write(f"! {aviso}\n")
    if corpo.get("circuito_otimizado_qasm"):
        saida.write("\n" + corpo["circuito_otimizado_qasm"])


def construir_parser():
    parser = argparse.ArgumentParser(prog="qone", description="Cliente de linha de comando do QuantumOne.")
    parser.add_argument("--api", default=API_PADRAO, help="URL da API (padrão: %(default)s)")
    parser.add_argument("--json", action="store_true", help="Imprime a resposta JSON completa.")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("saude", help="Confere se a API responde.")
    a = sub.add_parser("analisar", help="Análise completa: texto, código, arquivos e pastas (Qiskit + base + internet + LLM).")
    a.add_argument("mensagem", nargs="?", default="", help="Pedido em linguagem natural (opcional).")
    a.add_argument("--codigo", help="Arquivo de código ou '-' para ler da entrada padrão.")
    a.add_argument("--anexo", action="append", help="Arquivo ou pasta do projeto (pode repetir).")
    a.add_argument("--formato", choices=["auto", "qasm", "python"], default="auto")
    a.add_argument("--perfil", choices=["rapido", "balanceado", "avancado", "experimental"], default="avancado")
    a.add_argument("--top-k", type=int, default=15)
    a.add_argument("--sem-web", action="store_true", help="Ignorado: a análise sempre complementa a base com a internet.")
    a.add_argument("--conversa", help="conversation_id para continuar uma conversa.")
    sub.add_parser("limites", help="Mostra os limites do motor de circuitos.")
    p = sub.add_parser("perguntar", help="Pergunta à base de conhecimento local (RAG).")
    p.add_argument("texto")
    p.add_argument("--top-k", type=int, default=5)
    b = sub.add_parser("buscar", help="Recupera trechos da base local, sem gerar resposta.")
    b.add_argument("texto")
    b.add_argument("--top-k", type=int, default=5)
    d = sub.add_parser("diagnosticar", help="Diagnostica e corrige QASM, Python/Qiskit ou tracebacks.")
    d.add_argument("arquivo", help="Caminho do arquivo ou '-' para ler da entrada padrão.")
    d.add_argument("--formato", choices=["auto", "qasm", "python"], default="auto")
    d.add_argument("--politica", choices=["conservadora", "expandir_registradores"], default="conservadora")
    o = sub.add_parser("otimizar", help="Otimiza e verifica um circuito.")
    o.add_argument("arquivo")
    o.add_argument("--formato", choices=["auto", "qasm", "python"], default="auto")
    o.add_argument("--perfil", choices=["rapido", "balanceado", "avancado", "experimental"], default="balanceado")
    o.add_argument("--prioridade", choices=["dois_qubits", "profundidade", "total"], default="dois_qubits")
    o.add_argument("--topologia", choices=["todos", "linear", "anel", "grade", "heavy_hex"], default="todos")
    o.add_argument("--portas", choices=["cx_u", "ibm_cx", "ibm_heron", "ibm_eagle"], default=None)
    return parser


def executar(argv=None, cliente=None, saida=sys.stdout):
    args = construir_parser().parse_args(argv)
    proprio = cliente is None
    cliente = cliente or httpx.Client(base_url=args.api, timeout=180)
    try:
        if args.comando == "saude":
            resposta = cliente.get("/health")
        elif args.comando == "analisar":
            resposta = _analisar(cliente, args, saida)
        elif args.comando == "limites":
            resposta = cliente.get("/qiskit/limits")
        elif args.comando == "perguntar":
            resposta = cliente.post("/query", json={"input": args.texto, "top_k": args.top_k})
        elif args.comando == "buscar":
            resposta = cliente.post("/search", json={"query": args.texto, "top_k": args.top_k})
        elif args.comando == "diagnosticar":
            resposta = cliente.post("/qiskit/diagnose", json={"code": _ler(args.arquivo), "format": args.formato,
                                                              "politica": args.politica})
        else:
            corpo = {"code": _ler(args.arquivo), "format": args.formato, "perfil": args.perfil,
                     "prioridade": args.prioridade}
            if args.topologia != "todos" or args.portas:
                corpo["hardware"] = {"topologia": args.topologia, "portas_nativas": args.portas}
            resposta = cliente.post("/qiskit/optimize", json=corpo)
    except httpx.HTTPError as erro:
        saida.write(f"Não foi possível conectar à API em {args.api}: {type(erro).__name__}\n")
        return 2
    finally:
        if proprio:
            cliente.close()
    if resposta.status_code >= 400:
        saida.write(_erro(resposta) + "\n")
        return 1
    corpo = resposta.json()
    if args.json or args.comando in ("saude", "limites", "buscar"):
        saida.write(json.dumps(corpo, ensure_ascii=False, indent=2) + "\n")
    elif args.comando == "analisar":
        _imprimir_analise(corpo, saida)
    elif args.comando == "perguntar":
        _imprimir_resposta(corpo, saida)
    elif args.comando == "diagnosticar":
        _imprimir_diagnostico(corpo, saida)
    else:
        _imprimir_otimizacao(corpo, saida)
    return 0


if __name__ == "__main__":
    sys.exit(executar())
