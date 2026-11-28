"""A fila de LLMs usa transporte controlado aqui; Qiskit executa de verdade."""
from unittest.mock import Mock
import pytest
from tests.test_integracao import contexto
from rag import cliente_llm
from rag.contexto import preparar_contexto

QASM = 'OPENQASM 2.0; include "qelib1.inc"; qreg q[1]; h q[0]; h q[0];'

def _resposta(texto):
    return {"texto": texto, "modelo": "teste", "tentativas": [{"modelo": "teste", "resultado": "ok", "segundos": 0.1}]}

def test_rag_com_llm_controlada(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-apenas-de-teste")
    client.post("/documents", files={"file": ("guia.txt", b"Qiskit transpila circuitos.")})
    gerar = Mock(return_value=_resposta("Resposta controlada de teste [Fonte 1]."))
    monkeypatch.setattr(cliente_llm, "gerar_resposta", gerar)
    r = client.post("/query", json={"input": "Qiskit?"})
    assert r.status_code == 200
    assert r.json()["sources"][0]["source"] == "guia.txt"
    assert "[Fonte 1]" in gerar.call_args.args[1]
    assert r.json()["response"] == "Resposta controlada de teste."  # sem o marcador; a fonte segue em sources

def test_sem_contexto_nao_chama_llm(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("NVIDIA_API_KEY", "teste")
    gerar = Mock(side_effect=AssertionError("nao deveria chamar"))
    monkeypatch.setattr(cliente_llm, "gerar_resposta", gerar)
    r = client.post("/query", json={"input": "Qiskit?"})
    assert r.status_code == 200 and r.json()["status"] == "insufficient_context"
    gerar.assert_not_called()

def test_falha_llm_nao_expoe_credencial(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("NVIDIA_API_KEY", "teste")
    client.post("/documents", files={"file": ("guia.txt", b"Qiskit transpila circuitos.")})
    falha = cliente_llm.FilaEsgotada([{"modelo": "m", "resultado": "erro_RuntimeError", "segundos": 0.1}])
    monkeypatch.setattr(cliente_llm, "gerar_resposta", Mock(side_effect=falha))
    r = client.post("/query", json={"input": "Qiskit?"})
    assert r.status_code == 200 and r.json()["answer_mode"] == "local"
    assert "teste" not in r.text.replace("guia.txt", "")
    monkeypatch.setenv("QONE_LOCAL_FALLBACK", "0")
    r = client.post("/query", json={"input": "Qiskit?"})
    assert r.status_code == 503

def test_contexto_limitado():
    docs = [{"texto": "x"*2000, "metadados": {"titulo": "teste"}}]*3
    contexto, fontes = preparar_contexto(docs, limite=500)
    assert len(contexto) <= 500 and len(fontes) == 1

def test_qiskit_http_real(contexto):
    client, _ = contexto
    r = client.post("/qiskit/optimize", json={"qasm": QASM})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["equivalente"] is True
    assert d["original"]["total_operacoes"] == 2
    assert d["proposta"]["total_operacoes"] == 0

@pytest.mark.parametrize("body", [{"qasm":"invalido"}, {"qasm":QASM,"optimization_level":True},
    {"qasm":QASM,"optimization_level":4}, {"qasm":'OPENQASM 2.0; include "/etc/passwd";'}])
def test_qiskit_entrada_invalida(contexto, body):
    client, _ = contexto
    r = client.post("/qiskit/optimize", json=body)
    assert r.status_code == 422
    if r.json()["code"] == "HTTP_ERROR":
        assert "OpenQASM 2" in r.json()["message"]

def test_qiskit_limite_qubits(contexto):
    client, _ = contexto
    r = client.post("/qiskit/optimize", json={"qasm": "OPENQASM 2.0; qreg q[33];"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "CIRCUIT_LIMIT_EXCEEDED"

def test_qiskit_timeout(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("QISKIT_TIMEOUT_SECONDS", "0.001")
    assert client.post("/qiskit/optimize", json={"qasm":QASM}).status_code == 504
