"""Optativo: valida servidor, modelo e rede reais. Grava um documento de teste."""
import os
from uuid import uuid4

import httpx
import pytest


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("GROWUP_TEST_URL"), reason="Defina GROWUP_TEST_URL para validar os containers reais.")
def test_fluxo_real():
    with httpx.Client(base_url=os.environ["GROWUP_TEST_URL"], timeout=120) as client:
        assert client.get("/health").json() == {"status": "ok", "mode": "integrated"}
        nome = f"teste-integracao-{uuid4().hex}.txt"
        texto = "O teste de integração GrowUp confirma que a transpilação adapta circuitos ao dispositivo."
        resposta = client.post("/documents", files={"file": (nome, texto.encode("utf-8"))})
        assert resposta.status_code == 201, resposta.text
        id = resposta.json()["id"]
        repetido = client.post("/documents", files={"file": (nome, texto.encode("utf-8"))})
        assert repetido.status_code == 201 and repetido.json()["id"] == id
        assert any(d["id"] == id for d in client.get("/documents").json()["documents"])
        busca = client.post("/search", json={"query": texto})
        assert busca.status_code == 200, busca.text
        assert any(c["document_id"] == id for c in busca.json()["chunks"])
        consulta = client.post("/query", json={"input": "O que é transpilação?", "web_search": True})
        # Nunca há busca web; com ou sem Gemini a resposta vem da base local (LLM ou extrativa).
        assert consulta.status_code == 200, consulta.text
        corpo = consulta.json()
        assert corpo["web_status"] == "disabled" and corpo["answer_mode"] in ("llm", "local", "none")
        assert all(f["origin"] == "local" for f in corpo["sources"])
        diagnostico = client.post("/qiskit/diagnose", json={"code": "OPENQASM 2.0;\nqreg q[1];\nh q[3];\n"})
        assert diagnostico.status_code == 200 and diagnostico.json()["validacao"]["valido"] is True
