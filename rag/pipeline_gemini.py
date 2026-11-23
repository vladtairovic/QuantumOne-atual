"""Pipeline RAG do QuantumOne: somente base local.

Pergunta -> interpretação -> recuperação híbrida -> verificação de evidência
-> resposta (Gemini, se configurado; senão extrativa local).

A busca web foi removida: nenhuma etapa deste módulo consulta mecanismos de
busca nem envia a pergunta para a internet. O Gemini, quando configurado,
recebe somente a pergunta e os trechos locais, sem ferramenta de busca.
"""
import logging
import os

from . import busca_chromadb, cliente_gemini, resposta_local
from .contexto_gemini import preparar_contexto, MENSAGEM_SEM_CONTEXTO
from .interpretacao import interpretar

log = logging.getLogger(__name__)

AVISO_WEB_REMOVIDA = ("A busca web foi removida do QuantumOne; o pedido web_search=true foi ignorado "
                      "e a resposta usa somente a base local.")


def llm_configurada():
    return bool(os.getenv("GEMINI_API_KEY", "").strip()) and os.getenv("QONE_LLM", "auto").lower() != "local"


def fallback_local_ativo():
    return os.getenv("QONE_LOCAL_FALLBACK", "1") == "1"


def _fonte(d):
    meta = d["metadados"]
    return {"document_id": meta["document_id"], "chunk_id": d["id"], "source": meta["fonte"],
            "content": d["texto"], "origin": "local", "title": meta.get("titulo"),
            "retrieved_at": meta.get("coletado_em")}


def responder(pergunta, top_k=3, web_search=None):
    avisos = [AVISO_WEB_REMOVIDA] if web_search else []
    analise = interpretar(pergunta)
    documentos = busca_chromadb.buscar_documentos(
        pergunta, quantidade=top_k, biblioteca=analise["biblioteca_principal"])
    evidencia = busca_chromadb.avaliar_evidencia(pergunta, documentos)
    base = {"web_status": "disabled", "interpretation": analise, "evidence": evidencia}
    contexto, referencias = preparar_contexto(
        documentos, limite=int(os.getenv("GROWUP_MAX_CONTEXT_CHARS", "12000")))
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
        try:
            texto = cliente_gemini.gerar_resposta(pergunta, contexto)
            return {**base, "response": texto, "sources": fontes, "status": "ok",
                    "answer_mode": "llm", "warnings": avisos}
        except Exception as erro:
            if not fallback_local_ativo():
                raise
            # Não expõe detalhes do provedor; registra só o tipo do erro.
            log.warning("LLM indisponível (%s); usando resposta local.", type(erro).__name__)
            avisos.append("O modelo de linguagem não respondeu (limite de cota, rede ou configuração). "
                          "A resposta abaixo foi montada localmente a partir da base documental.")
    local = resposta_local.gerar(pergunta, usados)
    if local is None:
        return {**base, "response": MENSAGEM_SEM_CONTEXTO, "sources": fontes,
                "status": "insufficient_context", "answer_mode": "none", "warnings": avisos}
    # A numeração [Fonte N] segue a ordem de ``sources``; por isso a lista completa é mantida.
    return {**base, "response": local["response"], "sources": fontes,
            "status": "ok", "answer_mode": "local", "warnings": avisos}
