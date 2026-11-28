"""Regressão dourada: com todas as camadas RAG_* desligadas, o RAG devolve o mesmo que antes da branch.

``tests/fixtures/rag_caminho_padrao.json`` foi gerado a partir do commit ``e414f88`` (anterior à
branch ``feat/rag-profissional``); ver ``tests/gerar_rag_caminho_padrao.py`` para regenerá-lo."""
import json
from pathlib import Path

import pytest

import gerar_rag_caminho_padrao as gerador  # noqa: E402  (pasta tests no sys.path via conftest)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "rag_caminho_padrao.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def atual():
    from rag import busca_chromadb
    originais = (busca_chromadb.obter_colecao, busca_chromadb.criar_embeddings)
    try:
        yield gerador.calcular(FIXTURE["corpus"], FIXTURE["perguntas"])
    finally:
        busca_chromadb.obter_colecao, busca_chromadb.criar_embeddings = originais
        busca_chromadb.invalidar_indice_lexical()


def test_fixture_nao_e_vazia():
    assert len(FIXTURE["perguntas"]) == 20
    assert len(FIXTURE["busca"]) == 20 * 3 * 2
    assert sum(1 for v in FIXTURE["busca"].values() if v) > 100
    assert len(FIXTURE["contexto"]) == 4 * 3


def test_busca_padrao_identica_ao_codigo_anterior(atual):
    busca, _ = atual
    assert busca == FIXTURE["busca"]


def test_contexto_padrao_identico_ao_codigo_anterior(atual):
    _, contexto = atual
    assert contexto == FIXTURE["contexto"]
