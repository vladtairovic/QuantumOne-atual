"""Reindexa documentos/base_documental.json na coleção do modelo ativo.

Escreve somente na coleção definida por RAG_EMBEDDING_MODEL / RAG_CHUNK_TOKENS
(ver rag.configuracao.nome_colecao); coleções de outros modelos não são tocadas.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from rag import configuracao, indexar_documentos  # noqa: E402
from rag.banco import obter_colecao  # noqa: E402


def main():
    print("Modelo:", configuracao.MODELO_EMBEDDINGS)
    print("Coleção:", configuracao.NOME_COLECAO)
    print("Limite de tokens por chunk:", configuracao.LIMITE_CONFIGURADO)
    indexar_documentos.main()
    print("Contagem final na coleção", configuracao.NOME_COLECAO, ":", obter_colecao().count())


if __name__ == "__main__":
    main()
