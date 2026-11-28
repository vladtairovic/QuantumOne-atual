"""Pipeline RAG do QuantumOne: somente base local.

Pergunta -> interpretação -> recuperação híbrida -> verificação de evidência
-> resposta (fila de LLMs NVIDIA, se configurada; senão extrativa local).

A busca web foi removida: nenhuma etapa deste módulo consulta mecanismos de
busca. A fila de LLMs, quando configurada, recebe somente a pergunta e os
trechos locais, sem ferramenta de busca.
"""
import logging
import math
import os

from . import busca_chromadb, cliente_llm, configuracao, recursos, resposta_local, traco, verificacao
from .consulta import expandir_consulta
from .contexto import preparar_contexto, MENSAGEM_SEM_CONTEXTO
from .interpretacao import interpretar
from .texto import remover_marcadores_de_fonte

log = logging.getLogger(__name__)

AVISO_WEB_REMOVIDA = ("A busca web foi removida do QuantumOne; o pedido web_search=true foi ignorado "
                      "e a resposta usa somente a base local.")


def llm_configurada():
    if os.getenv("QONE_LLM", "auto").lower() == "local":
        return False
    return cliente_llm.configurada()


def fallback_local_ativo():
    return os.getenv("QONE_LOCAL_FALLBACK", "1") == "1"


# Contexto que o Desktop Chat acrescenta à mensagem (código do editor e anexos).
# Ele vai para a LLM, mas não deve diluir a busca nem a verificação de evidência.
_MARCADORES_CONTEXTO = ("\n\nCódigo atual (", "\n\nO editor está vazio.", "\n\nArquivo: ")


def extrair_consulta(texto):
    """Parte da mensagem que é a pergunta do usuário (sem contexto anexado)."""
    corte = min((texto.find(m) for m in _MARCADORES_CONTEXTO if m in texto), default=-1)
    pergunta = texto[:corte].strip() if corte > 0 else texto.strip()
    return pergunta or texto.strip()


def _fonte(d):
    meta = d["metadados"]
    return {"document_id": meta["document_id"], "chunk_id": d["id"], "source": meta["fonte"],
            "content": d["texto"], "origin": "local", "title": meta.get("titulo"),
            "retrieved_at": meta.get("coletado_em")}


def _fidelidade_minima(padrao=0.6):
    try:
        valor = float(os.getenv("RAG_MIN_FIDELIDADE", str(padrao)))
    except ValueError:
        return padrao
    return min(1.0, max(0.0, valor)) if math.isfinite(valor) else padrao


def _verificar(texto, fontes, avisos, base):
    """Verifica a resposta da LLM frase a frase; devolve True se ela deve ser descartada.

    Nunca lança: em caso de erro registra ``base["verificacao"] = {"erro": True}`` e mantém a resposta."""
    try:
        resultado = verificacao.verificar_resposta(texto, fontes)
        sem_suporte = [f["texto"] for f in resultado["frases"] if not f["suportada"]]
        fidelidade = resultado["fidelidade"]
        minimo = _fidelidade_minima()
    except Exception:
        log.warning("Verificação da resposta falhou; mantendo a resposta da LLM.", exc_info=True)
        traco.registrar_fallback("verificacao: falha ao verificar a resposta")
        base["verificacao"] = {"erro": True}
        return False
    for frase in sem_suporte[:5]:
        corte = frase if len(frase) <= 160 else frase[:159] + "…"
        avisos.append(f"Frase sem suporte nas fontes: \"{corte}\"")
    if len(sem_suporte) > 5:
        avisos.append(f"{len(sem_suporte)} frases sem suporte nas fontes")
    base["verificacao"] = {"fidelidade": fidelidade, "frases_sem_suporte": len(sem_suporte)}
    if fidelidade < minimo:
        avisos.append("A resposta da LLM foi descartada porque não estava sustentada pelas fontes "
                      f"(fidelidade {fidelidade:.0%}); a resposta abaixo foi montada localmente a partir da base documental.")
        return True
    return False


CAMADAS = ("reranker", "consulta", "mmr", "orcamento", "verificacao")


def responder(pergunta, top_k=3, web_search=None, consulta=None):
    """Responde e anexa ``trace`` (etapas, consultas, camadas, fallbacks) ao resultado de ``_responder``."""
    traco.iniciar()
    estado = {"tempos": {}, "consultas": [], "documentos": []}
    resultado = _responder(pergunta, top_k, web_search, consulta, estado)
    resultado["trace"] = {
        "etapas": {nome: round(float(s), 6) for nome, s in estado["tempos"].items()},
        "consultas": estado["consultas"], "candidatos": len(estado["documentos"]),
        "camadas": {nome: recursos.ativo(nome) for nome in CAMADAS},
        "modelo_embeddings": configuracao.MODELO_EMBEDDINGS, "fallbacks": traco.coletar()}
    return resultado


def _responder(pergunta, top_k, web_search, consulta, estado):
    """``pergunta`` vai para a LLM; ``consulta`` (a mensagem do usuário) guia a busca.

    Nas conversas, ``pergunta`` inclui instruções e histórico; usá-la na busca
    diluiria os termos e faria o filtro de evidência recusar perguntas válidas.
    """
    avisos = [AVISO_WEB_REMOVIDA] if web_search else []
    consulta = extrair_consulta(consulta if consulta is not None else pergunta)
    analise = interpretar(consulta)
    variantes = None
    if recursos.ativo("consulta") and llm_configurada():
        variantes = expandir_consulta(consulta, conversa=pergunta if pergunta != consulta else None, transporte=None)
        documentos = busca_chromadb.buscar_documentos(
            consulta, quantidade=top_k, biblioteca=analise["biblioteca_principal"], consultas=variantes,
            tempos=estado["tempos"])
    else:
        documentos = busca_chromadb.buscar_documentos(
            consulta, quantidade=top_k, biblioteca=analise["biblioteca_principal"], tempos=estado["tempos"])
    estado["consultas"] = list(variantes) if variantes is not None else [consulta]
    estado["documentos"] = documentos
    evidencia = busca_chromadb.avaliar_evidencia(consulta, documentos)
    base = {"web_status": "disabled", "interpretation": analise, "evidence": evidencia}
    if variantes is not None:
        base["variantes_consulta"] = variantes
    contexto, referencias = preparar_contexto(
        documentos, limite=int(os.getenv("GROWUP_MAX_CONTEXT_CHARS", "6000")))
    usados = [d for d in documentos if d.get("texto", "").strip()][:len(referencias)]
    if not contexto.strip() or not evidencia["suficiente"]:
        if documentos and not evidencia["suficiente"]:
            avisos.append("Foram encontrados trechos, mas eles não cobrem os termos principais da pergunta "
                          f"(cobertura {evidencia['cobertura']:.0%}). Nenhuma resposta foi gerada para evitar invenção.")
        return {**base, "response": MENSAGEM_SEM_CONTEXTO, "sources": [_fonte(d) for d in usados],
                "status": "insufficient_context", "answer_mode": "none", "warnings": avisos}
    if any(d["metadados"].get("desatualizado") for d in usados):
        avisos.append("Parte das fontes está marcada como desatualizada para a versão atual do Qiskit; "
                      "confira a versão antes de aplicar o código.")
    fontes = [_fonte(d) for d in usados]
    if llm_configurada():
        # Fila de prioridade NVIDIA: cada modelo tem até LLM_TIMEOUT_SECONDS para responder.
        try:
            gerado = cliente_llm.gerar_resposta(pergunta, contexto)
            pulados = [t["modelo"] for t in gerado["tentativas"] if t["resultado"] != "ok"]
            if pulados:
                avisos.append(f"Respondido por {gerado['modelo']} após {len(pulados)} modelo(s) da fila não responderem a tempo.")
            llm = {"provedor": "nvidia", "modelo": gerado["modelo"], "tentativas": gerado["tentativas"]}
            descartada = False
            if recursos.ativo("verificacao"):
                descartada = _verificar(gerado["texto"], fontes, avisos, base)
            if not descartada:
                # A verificação usa o texto original; quem lê vê o texto sem marcadores [Fonte N]
                # (as fontes já aparecem em "sources").
                return {**base, "response": remover_marcadores_de_fonte(gerado["texto"]), "sources": fontes, "status": "ok",
                        "answer_mode": "llm", "llm": llm, "warnings": avisos}
            base["llm"] = llm
        except cliente_llm.FilaEsgotada as erro:
            if not fallback_local_ativo():
                raise
            log.warning("Fila de LLMs esgotada: %s", [t["resultado"] for t in erro.tentativas])
            avisos.append("Nenhum modelo da fila respondeu dentro do tempo limite. "
                          "A resposta abaixo foi montada localmente a partir da base documental.")
            base["llm"] = {"provedor": "nvidia", "modelo": None, "tentativas": erro.tentativas}
    local = resposta_local.gerar(consulta, usados)
    if local is None:
        return {**base, "response": MENSAGEM_SEM_CONTEXTO, "sources": fontes,
                "status": "insufficient_context", "answer_mode": "none", "warnings": avisos}
    # A numeração [Fonte N] segue a ordem de ``sources``; por isso a lista completa é mantida.
    return {**base, "response": local["response"], "sources": fontes,
            "status": "ok", "answer_mode": "local", "warnings": avisos}
