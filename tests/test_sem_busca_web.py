"""Prioridade 0: nenhuma consulta normal usa busca web nem acessa a internet."""
import importlib
import socket
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rag import cliente_llm
from tests.test_integracao import contexto  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
DOC = ("Medição de qubits no Qiskit. Use QuantumCircuit.measure para medir um qubit em um bit clássico "
       "ou measure_all para medir todos os qubits. A medição colapsa o estado quântico.")


@pytest.fixture
def sem_rede(monkeypatch):
    """Falha qualquer conexão que não seja loopback (TestClient não usa sockets)."""
    tentativas = []
    original = socket.socket.connect

    def conectar(self, endereco):
        host = endereco[0] if isinstance(endereco, tuple) else str(endereco)
        if host not in ("127.0.0.1", "::1", "localhost"):
            tentativas.append(endereco)
            raise OSError("rede externa bloqueada pelo teste")
        return original(self, endereco)

    monkeypatch.setattr(socket.socket, "connect", conectar)
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("rede externa bloqueada")))
    return tentativas


def _enviar(client):
    resposta = client.post("/documents", files={"file": ("medicao.txt", DOC.encode())})
    assert resposta.status_code == 201


def test_modulo_de_busca_web_removido():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rag.busca_web")


def test_codigo_fonte_nao_referencia_ferramenta_de_busca():
    proibidos = ("google_search", "GoogleSearch", "WEB_SEARCH_ENABLED", "GEMINI_WEB_MODEL", "GEMINI_API_KEY",
                 "google.genai", "google-genai", "cliente_gemini",
                 "Buscar também na web", "Consultar a internet")
    arquivos = [p for p in ROOT.rglob("*") if p.suffix in (".py", ".ts", ".tsx", ".yaml", ".example")
                and "node_modules" not in p.parts and ".next" not in p.parts and "tests" not in p.parts]
    achados = [(str(p.relative_to(ROOT)), termo) for p in arquivos
               for termo in proibidos if termo in p.read_text(encoding="utf-8", errors="ignore")]
    assert achados == []


def test_consulta_sem_llm_responde_offline_com_base_local(contexto, sem_rede):
    client, _ = contexto
    _enviar(client)
    r = client.post("/query", json={"input": "Como medir qubits no Qiskit?"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["status"] == "ok" and corpo["answer_mode"] == "local"
    assert corpo["web_status"] == "disabled"
    assert "[Fonte 1]" in corpo["response"] and "measure" in corpo["response"]
    assert all(f["origin"] == "local" for f in corpo["sources"])
    assert sem_rede == []


def test_web_search_true_e_ignorado_com_aviso(contexto, sem_rede):
    client, _ = contexto
    _enviar(client)
    r = client.post("/query", json={"input": "Como medir qubits no Qiskit?", "web_search": True})
    assert r.status_code == 200
    assert r.json()["web_status"] == "disabled"
    assert any("busca web foi removida" in a for a in r.json()["warnings"])
    assert sem_rede == []


def test_fila_llm_nunca_recebe_ferramenta_de_busca(contexto, monkeypatch, sem_rede):
    import httpx
    client, _ = contexto
    _enviar(client)
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("NVIDIA_MODELS", "m1")
    cliente_llm.limpar_pausas()
    corpos = []
    original = cliente_llm.gerar_resposta

    def responder(request):
        import json as _json
        corpos.append(_json.loads(request.content))
        return httpx.Response(429, json={"detail": "chave-de-teste"})
    monkeypatch.setattr(cliente_llm, "gerar_resposta",
                        lambda p, c: original(p, c, transporte=httpx.MockTransport(responder)))
    r = client.post("/query", json={"input": "Como medir qubits no Qiskit?", "web_search": True})
    cliente_llm.limpar_pausas()
    assert r.status_code == 200 and r.json()["answer_mode"] == "local"
    assert "chave-de-teste" not in r.text
    assert len(corpos) == 1 and set(corpos[0]) == {"model", "messages", "max_tokens", "temperature", "stream"}
    assert sem_rede == []


def test_modo_local_forcado_nao_chama_llm_mesmo_com_chave(contexto, monkeypatch):
    client, _ = contexto
    _enviar(client)
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("QONE_LLM", "local")
    monkeypatch.setattr(cliente_llm, "gerar_resposta", lambda *a, **k: pytest.fail("LLM chamada no modo local"))
    assert client.post("/query", json={"input": "Como medir qubits no Qiskit?"}).json()["answer_mode"] == "local"


def test_pergunta_sem_evidencia_nao_gera_resposta(contexto, sem_rede):
    client, _ = contexto
    _enviar(client)
    corpo = client.post("/query", json={"input": "Qual a receita de bolo de cenoura com chocolate?"}).json()
    assert corpo["status"] == "insufficient_context" and corpo["answer_mode"] == "none"
    assert corpo["evidence"]["suficiente"] is False


def test_conversa_tambem_nao_usa_web(contexto, sem_rede, monkeypatch, tmp_path):
    monkeypatch.setenv("CONVERSAS_PATH", str(tmp_path / "conversas.sqlite"))
    client, _ = contexto
    _enviar(client)
    conversa = client.post("/conversations", json={}).json()["conversation_id"]
    r = client.post(f"/conversations/{conversa}/messages",
                    json={"input": "Como medir qubits no Qiskit?", "message_id": str(uuid4()), "web_search": True})
    assert r.status_code == 200 and r.json()["web_status"] == "disabled"
    assert sem_rede == []


def test_sincronizacao_automatica_desligada_por_padrao():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "KNOWLEDGE_AUTO_SYNC: ${KNOWLEDGE_AUTO_SYNC:-0}" in compose


def test_conversa_busca_pela_mensagem_e_nao_pelo_preambulo(contexto, monkeypatch, tmp_path):
    """Regressão: o preâmbulo da conversa e o contexto do editor diluíam a evidência."""
    monkeypatch.setenv("CONVERSAS_PATH", str(tmp_path / "conversas.sqlite"))
    client, _ = contexto
    _enviar(client)
    conversa = client.post("/conversations", json={}).json()["conversation_id"]
    mensagem = "Como medir qubits no Qiskit?\n\nO editor está vazio."
    r = client.post(f"/conversations/{conversa}/messages",
                    json={"input": mensagem, "message_id": str(uuid4())})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    assert r.json()["answer_mode"] == "local"
    assert r.json()["interpretation"]["tipo_entrada"] == "linguagem_natural"
