from uuid import uuid4

from fastapi.testclient import TestClient

import main


def test_conversa_retomada_e_reenvio(monkeypatch, tmp_path):
    monkeypatch.setenv("CONVERSAS_PATH", str(tmp_path / "conversas.sqlite"))
    consultas = []

    async def consultar(pergunta, top_k, web_search):
        consultas.append((pergunta, top_k, web_search))
        return {"response": "Sugestão de teste", "sources": [], "status": "ok"}

    monkeypatch.setattr(main.servicos, "consultar", consultar)
    cliente = TestClient(main.app)
    conversa = cliente.post("/conversations", json={}).json()["conversation_id"]
    caminho = f"/conversations/{conversa}"
    primeira = {"input": "Analise qc.x(2)", "message_id": str(uuid4()), "top_k": 8, "web_search": False}
    resultado = cliente.post(caminho + "/messages", json=primeira)
    assert resultado.status_code == 200
    assert cliente.post(caminho + "/messages", json=primeira).json() == resultado.json()
    assert len(consultas) == 1
    segunda = dict(primeira, message_id=str(uuid4()), input="Aprovo a sugestão")
    assert cliente.post(caminho + "/messages", json=segunda).status_code == 200
    assert "qc.x(2)" in consultas[-1][0] and "Aprovo" in consultas[-1][0]
    assert consultas[-1][1:] == (8, False)
    assert len(cliente.get(caminho).json()["messages"]) == 4
    assert cliente.post(caminho + "/messages", json=dict(primeira, input="Outra")).status_code == 409
