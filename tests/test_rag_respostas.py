import json

import pytest

from rag import glossario, suporte
from rag.avaliacao_rag import carregar_respostas, fidelidade_llm, resumir

FONTE = "Entanglement is created by applying a Hadamard gate followed by a CNOT gate on two qubits."
UNREL = "A receita de bolo exige farinha, ovos e acucar misturados."


def _arquivo(tmp_path, dados):
    caminho = tmp_path / "r.json"
    caminho.write_text(json.dumps(dados), encoding="utf-8")
    return caminho


def _ok(i="a"):
    return {"id": i, "resposta": FONTE, "fontes": [FONTE]}


def test_carrega_respostas_validas(tmp_path):
    assert carregar_respostas(_arquivo(tmp_path, [_ok("a"), _ok("b")]), {"a", "b", "c"}).keys() == {"a", "b"}


@pytest.mark.parametrize("dados", [
    {"id": "a"},
    ["x"],
    [{"id": "a", "resposta": "t", "fontes": "texto"}],
    [{"id": "a", "fontes": ["f"]}],
    [{"id": 1, "resposta": "t", "fontes": ["f"]}],
    [{"id": "a", "resposta": "t", "fontes": [1]}],
])
def test_formato_invalido_gera_erro_claro(tmp_path, dados):
    with pytest.raises(ValueError, match="Formato"):
        carregar_respostas(_arquivo(tmp_path, dados), {"a"})


def test_ids_duplicados_geram_erro(tmp_path):
    with pytest.raises(ValueError, match="duplicados"):
        carregar_respostas(_arquivo(tmp_path, [_ok("a"), _ok("a")]), {"a"})


def test_ids_desconhecidos_sao_listados(tmp_path):
    with pytest.raises(ValueError, match="zzz"):
        carregar_respostas(_arquivo(tmp_path, [_ok("a"), _ok("zzz")]), {"a"})


def test_fidelidade_llm_media_por_caso_nao_por_frase():
    longa = " ".join([FONTE] * 9)
    respostas = {"a": {"resposta": longa, "fontes": [FONTE]},
                 "b": {"resposta": UNREL, "fontes": [FONTE]}}
    nota_b = suporte.pontuar_suporte(UNREL, [FONTE])
    assert fidelidade_llm(respostas) == round((1.0 + nota_b) / 2, 4)
    assert fidelidade_llm(respostas) < 0.7  # pooling por frase daria ~0.9


def test_fidelidade_llm_sem_frases_pontuaveis_e_none():
    assert fidelidade_llm({"a": {"resposta": "curta", "fontes": [FONTE]}}) is None
    assert fidelidade_llm({}) is None


def test_resumir_nao_inclui_fidelidade_llm():
    linha = {"id": "x", "tema": "t", "latencia_s": 0.1, "evidencia_suficiente": True, "cobertura": 1.0,
             "recall": 1.0, "precisao": 0.2, "rr": 1.0, "ndcg": 1.0, "citacoes": 1, "citacoes_corretas": 1,
             "frases_literais": 1, "frases": 1, "fidelidade_local": 0.8}
    resumo = resumir([linha], 5)
    assert resumo["fidelidade_resposta_local"] == 0.8
    assert "fidelidade_llm" not in resumo


def test_suporte_usa_glossario_expandir(monkeypatch):
    chamadas = []
    original = glossario.expandir
    monkeypatch.setattr(suporte, "expandir", lambda t: chamadas.append(t) or original(t))
    suporte.pontuar_suporte("O emaranhamento usa porta Hadamard.", [FONTE])
    assert chamadas
