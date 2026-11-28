import pytest

from rag import recursos
from rag.suporte import pontuar_suporte

FONTE = ("Entanglement is created by applying a Hadamard gate followed by a CNOT gate "
         "on two qubits of the QuantumCircuit.")
FONTES = [FONTE, "The transpile function maps a circuit onto the coupling map of a backend."]


def test_frase_copiada_da_fonte_tem_suporte_alto():
    assert pontuar_suporte(FONTE, FONTES) >= 0.9


def test_frase_de_outro_tema_tem_suporte_baixo():
    assert pontuar_suporte("A receita de bolo exige farinha, ovos e acucar misturados.", FONTES) <= 0.3


def test_parafrase_em_portugues_com_glossario_supera_frase_nao_relacionada():
    parafrase = pontuar_suporte("O emaranhamento nasce de uma porta Hadamard seguida de CNOT entre dois qubits.", FONTES)
    irrelevante = pontuar_suporte("A receita de bolo exige farinha, ovos e acucar misturados.", FONTES)
    assert parafrase > irrelevante
    assert parafrase >= 0.5


def test_sem_fontes_retorna_zero():
    assert pontuar_suporte(FONTE, []) == 0.0


def test_embedding_eleva_o_suporte_e_resultado_fica_em_0_1():
    embed = lambda textos: [[1.0, 0.0] for _ in textos]
    pontuacao = pontuar_suporte("A receita de bolo exige farinha, ovos e acucar.", FONTES, embed)
    assert pontuacao == pytest.approx(1.0)
    assert 0.0 <= pontuar_suporte(FONTE, FONTES, lambda t: [[0.0, 1.0], [-1.0, 0.0], [-1.0, 0.0]][:len(t)]) <= 1.0


def test_recurso_desligado_por_padrao_e_ligado_por_env(monkeypatch):
    monkeypatch.delenv("RAG_RERANKER", raising=False)
    assert recursos.ativo("reranker") is False
    monkeypatch.setenv("RAG_RERANKER", "1")
    assert recursos.ativo("reranker") is True
    monkeypatch.setenv("RAG_RERANKER", "0")
    assert recursos.ativo("reranker") is False
