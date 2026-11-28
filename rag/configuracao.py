"""Configuração compartilhada: indexação e busca usam o mesmo modelo e servidor."""
import hashlib
import logging
import os
import re
from functools import lru_cache
from threading import RLock
from typing import Literal

MODELO_LEGADO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LIMITE_LEGADO = 110
LIMITE_NOME_COLECAO = 63  # limite do ChromaDB para nomes de coleção
COLECAO_LEGADA = "qiskit_documentacao_multilingue"
MODELO_EMBEDDINGS = os.getenv("RAG_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or MODELO_LEGADO
MODELO_LOCK = RLock()
LIMITE_MINIMO = 16  # abaixo disso o chunk perde o sentido
log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def obter_modelo():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        MODELO_EMBEDDINGS,
        device="cpu",
        local_files_only=os.getenv("GROWUP_PERMITIR_DOWNLOAD", "0") != "1",
        trust_remote_code=False,
    )


def prefixo(tipo: Literal["consulta", "passagem"], modelo: str = MODELO_EMBEDDINGS) -> str:
    # Modelos e5 foram treinados com "query: "/"passage: "; os demais não usam prefixo.
    if "e5" not in modelo.lower():
        return ""
    return "query: " if tipo == "consulta" else "passage: "


def _limite_configurado() -> int:
    """``RAG_CHUNK_TOKENS`` validado: inteiro >= 16; qualquer outra coisa vale o padrão (110)."""
    bruto = os.getenv("RAG_CHUNK_TOKENS", "").strip()
    if not bruto:
        return LIMITE_LEGADO
    try:
        valor = int(bruto)
    except ValueError:
        log.warning("RAG_CHUNK_TOKENS=%r não é um inteiro; usando %d.", bruto, LIMITE_LEGADO)
        return LIMITE_LEGADO
    if valor < LIMITE_MINIMO:
        log.warning("RAG_CHUNK_TOKENS=%d abaixo do mínimo (%d); usando %d.", valor, LIMITE_MINIMO, LIMITE_LEGADO)
        return LIMITE_LEGADO
    return valor


def limite_tokens(modelo=None) -> int:
    modelo = modelo if modelo is not None else obter_modelo()
    return min(_limite_configurado(), modelo.max_seq_length)


def estrategia_chunks(limite: int) -> str:
    return "secoes_tokens_v2" if limite == LIMITE_LEGADO else f"secoes_tokens_v2_{limite}"


def nome_colecao(modelo: str, limite: int) -> str:
    explicito = os.getenv("CHROMA_COLLECTION")
    if explicito:
        return explicito
    if modelo == MODELO_LEGADO and limite == LIMITE_LEGADO:
        return COLECAO_LEGADA
    slug = re.sub(r"[^a-z0-9]+", "_", modelo.lower()).strip("_")
    nome = f"qone_rag_{slug}_{limite}"
    if len(nome) <= LIMITE_NOME_COLECAO:
        return nome
    # Nome longo: trunca o slug e acrescenta hash estável de modelo e limite,
    # para que modelos distintos com o mesmo prefixo não colidam.
    resumo = hashlib.sha1(f"{modelo}|{limite}".encode("utf-8")).hexdigest()[:8]
    sufixo = f"_{limite}_{resumo}"
    sobra = LIMITE_NOME_COLECAO - len("qone_rag_") - len(sufixo)
    return f"qone_rag_{slug[:sobra].rstrip('_')}{sufixo}"


# Limite usado para nomear a coleção sem carregar o modelo (o corte por
# max_seq_length só afeta modelos com janela menor que o limite configurado).
LIMITE_CONFIGURADO = _limite_configurado()
NOME_COLECAO = nome_colecao(MODELO_EMBEDDINGS, LIMITE_CONFIGURADO)


def criar_embeddings(tipo: Literal["consulta", "passagem"] = "consulta"):
    # A mesma função é usada na escrita e na consulta. Nenhum embedding é gerado
    # pelo servidor e não há fallback para o modelo padrão do ChromaDB.
    def gerar(textos):
        from .cache import recuperar, salvar
        marca = prefixo(tipo, MODELO_EMBEDDINGS)
        textos = [marca + t for t in textos] if marca else list(textos)
        vetor = recuperar(textos, MODELO_EMBEDDINGS)
        if vetor is not None:
            return vetor
        with MODELO_LOCK:
            vetor = obter_modelo().encode(
                textos, normalize_embeddings=True, convert_to_numpy=True,
                show_progress_bar=False,
            ).tolist()
        salvar(textos, MODELO_EMBEDDINGS, vetor)
        return vetor
    return gerar
