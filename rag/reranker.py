"""Reranker neural opcional (cross-encoder) aplicado após a fusão RRF.

Desligado por padrão: só age com ``RAG_RERANKER=1`` (ver ``recursos.ativo``).
O modelo vem de ``RAG_RERANKER_MODEL`` (nome no Hugging Face ou pasta local) e
é carregado sob demanda, uma única vez, com ``local_files_only`` a menos que
``GROWUP_PERMITIR_DOWNLOAD=1`` e ``trust_remote_code=False``. Sem modelo
configurado ou se a carga falhar, ``reordenar`` registra um aviso (uma vez) e
devolve a lista de entrada intacta: a ausência do modelo nunca derruba a busca.

Mistura de notas (feita em ``busca_chromadb.buscar_documentos``): entre os
candidatos reordenados, a nota neural e a nota RRF recebem normalização
min-max; ``RAG_RERANKER_PESO`` (0..1, padrão 0.7) é o peso da nota neural, e
o resultado é mapeado linearmente para [min, max] das notas RRF da própria
cabeça reranqueada (peso 0 -> min, peso 1 -> max), de modo que ``pontuacao``
continua na escala RRF e o corte ``0.7 * max`` de ``resposta_local`` se comporta
como sem o reranker; só então se aplicam os mesmos multiplicadores de regra de antes (título,
identificador, biblioteca, obsoleto). O peso deve ser calibrado com o benchmark.

Ganchos para testes: ``reordenar(..., pontuador=...)`` injeta um callable
``(pares) -> list[float]`` por chamada; ``_PONTUADOR_PADRAO`` (módulo) faz o
mesmo para chamadas que não passam ``pontuador`` (usado via ``busca_chromadb``).
"""
import logging
import math
import os
import threading
import time

from . import recursos, traco

log = logging.getLogger(__name__)

_PONTUADOR_PADRAO = None
_LOCK = threading.Lock()
_ESTADO = {"tentou": False, "modelo": None, "aquecido": False}
_AVISADOS = set()


def _resetar():
    with _LOCK:
        _ESTADO.update(tentou=False, modelo=None, aquecido=False)
        _AVISADOS.clear()


def _avisar_uma_vez(motivo, mensagem, *args):
    traco.registrar_fallback(f"reranker: {motivo}")
    with _LOCK:
        if motivo in _AVISADOS:
            return
        _AVISADOS.add(motivo)
    log.warning(mensagem, *args)


def _criar_modelo(nome):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        nome,
        device="cpu",
        local_files_only=os.getenv("GROWUP_PERMITIR_DOWNLOAD", "0") != "1",
        trust_remote_code=False,
    )


def _obter_modelo():
    """Carrega o cross-encoder uma vez (sob lock); devolve None se indisponível."""
    with _LOCK:
        if _ESTADO["tentou"]:
            return _ESTADO["modelo"]
        _ESTADO["tentou"] = True
        nome = os.getenv("RAG_RERANKER_MODEL", "").strip()
        if not nome:
            log.warning("Reranker ligado, mas RAG_RERANKER_MODEL não está definido; mantendo a ordem original.")
            return None
        try:
            _ESTADO["modelo"] = _criar_modelo(nome)
        except Exception as erro:  # noqa: BLE001 - qualquer falha de carga deve cair no fallback
            log.warning("Reranker '%s' indisponível (%s: %s); mantendo a ordem original.",
                        nome, type(erro).__name__, erro)
        return _ESTADO["modelo"]


def pre_carregar() -> bool:
    """Carrega e aquece o cross-encoder na partida da API (uma passada de teste no ``predict``).

    Só age com ``RAG_RERANKER=1`` e modelo configurado. Idempotente (reaproveita ``_ESTADO``: uma carga que
    falhou não é repetida) e nunca levanta: em qualquer falha devolve False e registra um aviso sem
    traceback; as consultas seguintes caem no fallback seguro de ``reordenar``. Não baixa nada a menos que
    ``GROWUP_PERMITIR_DOWNLOAD=1`` (a regra vem de ``_criar_modelo``)."""
    try:
        if not recursos.ativo("reranker") or not os.getenv("RAG_RERANKER_MODEL", "").strip():
            return False
        with _LOCK:
            if _ESTADO["aquecido"]:
                return True
        inicio = time.monotonic()
        modelo = _obter_modelo()
        if modelo is None:
            return False
        modelo.predict([("aquecimento do reranker", "texto de aquecimento do reranker")])
        with _LOCK:
            _ESTADO["aquecido"] = True
        # Canal do uvicorn: o INFO da aplicação não aparece nos logs do contêiner (logging sem configuração).
        logging.getLogger("uvicorn.error").info("Reranker '%s' carregado e aquecido em %.1f s.",
                 os.getenv("RAG_RERANKER_MODEL", "").strip(), time.monotonic() - inicio)
        return True
    except Exception as erro:  # noqa: BLE001 - o aquecimento nunca pode impedir a API de subir
        log.warning("Aquecimento do reranker falhou (%s); as consultas usarão a ordem original.",
                    type(erro).__name__)
        return False


def disponivel(_cache_dir=None) -> bool:
    """Indica se há modelo utilizável SEM rede: pasta local, ou ``config.json`` no cache do HF.

    Não exige todos os arquivos do repositório (caches enxutos servem) e nunca levanta."""
    with _LOCK:
        if _ESTADO["tentou"]:
            return _ESTADO["modelo"] is not None
    nome = os.getenv("RAG_RERANKER_MODEL", "").strip()
    if not nome:
        return False
    try:
        if os.path.isdir(nome):
            return True
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo_id=nome, filename="config.json", cache_dir=_cache_dir), str)
    except Exception:  # noqa: BLE001
        return False


def reordenar(pergunta, candidatos, topo=30, pontuador=None):
    """Reordena os ``topo`` primeiros candidatos pela nota neural.

    Os demais ficam depois, na ordem original. Os reordenados ganham
    ``nota_reranker``. Sem pontuador/modelo, devolve ``candidatos`` intacto.
    """
    if not candidatos:
        return []
    pontuador = pontuador or _PONTUADOR_PADRAO
    if pontuador is None:
        modelo = _obter_modelo()
        if modelo is None:
            traco.registrar_fallback("reranker: modelo indisponível")
            return candidatos
        pontuador = modelo.predict
    cabeca, cauda = candidatos[:topo], candidatos[topo:]
    try:
        notas = [float(n) for n in pontuador([(pergunta, c["texto"]) for c in cabeca])]
    except Exception as erro:  # noqa: BLE001 - falha de inferência nunca deve virar erro na busca
        _avisar_uma_vez("inferencia", "Falha ao pontuar com o reranker (%s: %s); mantendo a ordem original.",
                        type(erro).__name__, erro)
        return candidatos
    if len(notas) != len(cabeca):
        _avisar_uma_vez("contagem", "Reranker devolveu %d notas para %d candidatos; mantendo a ordem original.",
                        len(notas), len(cabeca))
        return candidatos
    if not all(math.isfinite(n) for n in notas):
        _avisar_uma_vez("nao_finita", "Reranker devolveu nota não finita; mantendo a ordem original.")
        return candidatos
    pontuados = [{**c, "nota_reranker": n} for c, n in zip(cabeca, notas)]
    pontuados.sort(key=lambda c: -c["nota_reranker"])
    return pontuados + list(cauda)
