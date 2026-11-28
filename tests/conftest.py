"""Configuração comum dos testes."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))  # permite importar utilitários desta pasta (ex.: o gerador dourado)

_FIXAS = ("CHROMA_COLLECTION", "EMBEDDING_MODEL", "GROWUP_MAX_CONTEXT_CHARS", "QONE_LOCAL_CORTE")


@pytest.fixture(autouse=True)
def _ambiente_sem_variaveis_do_rag(monkeypatch):
    """Variáveis exportadas no shell (RAG_*, coleção, modelo, limites) não vazam para os testes."""
    for nome in [n for n in os.environ if n.startswith("RAG_")] + list(_FIXAS):
        monkeypatch.delenv(nome, raising=False)
