"""QuantumOne CLI: usa o mesmo backend do Desktop Chat.

Exemplos:
    python qone_cli.py saude
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

import httpx

API_PADRAO = os.getenv("QONE_API_URL", "http://127.0.0.1:8000")


def _ler(caminho):
    if caminho == "-":
        return sys.stdin.read()
    with open(caminho, encoding="utf-8") as arquivo:
        return arquivo.read()


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
    elif args.comando == "perguntar":
        _imprimir_resposta(corpo, saida)
    elif args.comando == "diagnosticar":
        _imprimir_diagnostico(corpo, saida)
    else:
        _imprimir_otimizacao(corpo, saida)
    return 0


if __name__ == "__main__":
    sys.exit(executar())
