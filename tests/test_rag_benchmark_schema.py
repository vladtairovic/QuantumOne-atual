"""Restrições do gabarito de avaliação do RAG (tests/rag_benchmark.json)."""
import json
from collections import Counter
from pathlib import Path

import pytest

from rag import conhecimento_interno

RAIZ = Path(__file__).resolve().parents[1]
GABARITO = RAIZ / "tests" / "rag_benchmark.json"
SNAPSHOTS = RAIZ / "artifacts" / "conhecimento"

IDS_ANTIGOS = (
    [f"t{i:02d}" for i in range(1, 7)] + ["b01", "b02", "m01", "m02", "m03", "m04"]
    + [f"g{i:02d}" for i in range(1, 6)] + ["p01", "p02", "q01", "q02", "q03", "q04"]
    + [f"s{i:02d}" for i in range(1, 7)] + [f"i{i:02d}" for i in range(1, 5)]
    + [f"c{i:02d}" for i in range(1, 5)] + [f"a{i:02d}" for i in range(1, 5)]
    + ["f01", "f02", "f03", "f04", "e01", "e02"] + [f"u{i:02d}" for i in range(1, 7)]
)


@pytest.fixture(scope="module")
def casos():
    return json.loads(GABARITO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def documentos_existentes():
    ids = set()
    for arquivo in SNAPSHOTS.glob("*.json"):
        if arquivo.name.endswith(".previous.json") or arquivo.name == "status.json":
            continue
        ids.add(json.loads(arquivo.read_text(encoding="utf-8"))["document_id"])
    ids.update(d["document_id"] for d in conhecimento_interno.carregar())
    return ids


def test_tamanho_e_ids_unicos(casos):
    assert len(casos) >= 140
    ids = [c["id"] for c in casos]
    assert len(ids) == len(set(ids))
    assert len(IDS_ANTIGOS) == 53
    assert set(IDS_ANTIGOS) <= set(ids)


def test_campos_obrigatorios(casos):
    for c in casos:
        assert c["pergunta"].strip() and c["tema"].strip(), c["id"]
        assert c["split"] in {"dev", "reservado"}, c["id"]
        assert c["idioma"] in {"pt", "en"}, c["id"]
    assert {c["idioma"] for c in casos} == {"pt", "en"}


def test_divisao_reservada(casos):
    fracao = sum(c["split"] == "reservado" for c in casos) / len(casos)
    assert 0.30 <= fracao <= 0.40, fracao


def test_fora_do_dominio_nos_dois_splits(casos):
    fora = [c for c in casos if not c.get("relevantes")]
    assert len(fora) >= 12
    assert {c["split"] for c in fora} == {"dev", "reservado"}


def test_casos_comparativos(casos):
    assert sum(c.get("tipo") == "comparativa" for c in casos) >= 15


def test_relevantes_existem_no_corpus(casos, documentos_existentes):
    for c in casos:
        for documento in c.get("relevantes", []):
            assert documento in documentos_existentes, f"{c['id']}: {documento}"


def test_temas_presentes_nos_dois_splits(casos):
    por_tema = {}
    for c in casos:
        por_tema.setdefault(c["tema"], Counter())[c["split"]] += 1
    # Temas com pelo menos 4 casos devem aparecer em dev e em reservado.
    for tema, contagem in por_tema.items():
        if sum(contagem.values()) >= 4:
            assert contagem["dev"] and contagem["reservado"], tema
