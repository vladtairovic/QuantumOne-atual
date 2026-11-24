"""Pipeline RAG do QuantumOne: somente base local.

Pergunta -> interpretação -> recuperação híbrida -> verificação de evidência
-> resposta (fila de LLMs NVIDIA, se configurada; senão extrativa local).

A busca web foi removida: nenhuma etapa deste módulo consulta mecanismos de
busca. A fila de LLMs, quando configurada, recebe somente a pergunta e os
trechos locais, sem ferramenta de busca.
"""
import logging
import os

from . import busca_chromadb, cliente_llm, resposta_local
from .contexto import preparar_contexto, MENSAGEM_SEM_CONTEXTO
from .interpretacao import interpretar

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


def responder(pergunta, top_k=3, web_search=None, consulta=None):
    """``pergunta`` vai para a LLM; ``consulta`` (a mensagem do usuário) guia a busca.

    Nas conversas, ``pergunta`` inclui instruções e histórico; usá-la na busca
    diluiria os termos e faria o filtro de evidência recusar perguntas válidas.
    """
    avisos = [AVISO_WEB_REMOVIDA] if web_search else []
    consulta = extrair_consulta(consulta if consulta is not None else pergunta)
    analise = interpretar(consulta)
    documentos = busca_chromadb.buscar_documentos(
        consulta, quantidade=top_k, biblioteca=analise["biblioteca_principal"])
    evidencia = busca_chromadb.avaliar_evidencia(consulta, documentos)
    base = {"web_status": "disabled", "interpretation": analise, "evidence": evidencia}
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
            return {**base, "response": gerado["texto"], "sources": fontes, "status": "ok", "answer_mode": "llm",
                    "llm": {"provedor": "nvidia", "modelo": gerado["modelo"], "tentativas": gerado["tentativas"]},
                    "warnings": avisos}
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
