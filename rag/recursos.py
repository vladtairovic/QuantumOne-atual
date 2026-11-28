"""Flags de camada do RAG: cada camada nova liga com ``RAG_<NOME>=1``."""
import os


def ativo(nome: str) -> bool:
    return os.environ.get(f"RAG_{nome.upper()}") == "1"
