import pytest

from rag.avaliacao_rag import filtrar_casos

CASOS = [{"id": "a"}, {"id": "b", "split": "dev"}, {"id": "c", "split": "reservado"}]


def test_filtra_por_split_e_trata_ausente_como_dev():
    assert [c["id"] for c in filtrar_casos(CASOS, "dev")] == ["a", "b"]
    assert [c["id"] for c in filtrar_casos(CASOS, "reservado")] == ["c"]
    assert len(filtrar_casos(CASOS, "todos")) == 3


def test_split_vazio_gera_erro_claro():
    with pytest.raises(ValueError, match="Nenhum caso"):
        filtrar_casos([{"id": "a", "split": "dev"}], "reservado")
