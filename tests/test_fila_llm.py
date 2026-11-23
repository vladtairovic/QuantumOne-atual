"""Fila de prioridade de LLMs (NVIDIA): ordem, limite de tempo, disjuntor e fallback local."""
import json
import time

import httpx
import pytest

from rag import cliente_llm
from tests.test_integracao import contexto  # noqa: F401  (fixture)
from tests.test_sem_busca_web import _enviar

FILA = ["forte", "medio", "fraco"]


@pytest.fixture(autouse=True)
def configuracao(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("NVIDIA_MODELS", ",".join(FILA))
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("LLM_PAUSA_SEGUNDOS", "60")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cliente_llm.limpar_pausas()
    yield
    cliente_llm.limpar_pausas()


def transporte(comportamento, chamadas):
    """comportamento: modelo -> ("ok", texto) | ("lento", s) | ("http", código) | ("vazio", None)."""
    def responder(request):
        corpo = json.loads(request.content)
        chamadas.append(corpo)
        tipo, valor = comportamento[corpo["model"]]
        if tipo == "lento":
            time.sleep(valor)
            tipo, valor = "ok", "tarde demais"
        if tipo == "http":
            return httpx.Response(valor, json={"detail": "falha"})
        conteudo = "" if tipo == "vazio" else valor
        return httpx.Response(200, json={"choices": [{"message": {"content": conteudo}}]})
    return httpx.MockTransport(responder)


def test_primeiro_da_fila_responde():
    chamadas = []
    r = cliente_llm.gerar_resposta("p", "c", transporte({"forte": ("ok", "Resposta [Fonte 1]")}, chamadas))
    assert r["modelo"] == "forte" and r["texto"] == "Resposta [Fonte 1]"
    assert len(chamadas) == 1
    # Só a pergunta e os trechos locais: nenhuma ferramenta (busca web) é enviada.
    assert set(chamadas[0]) == {"model", "messages", "max_tokens", "temperature"}
    assert chamadas[0]["messages"][0]["role"] == "system"


def test_modelo_lento_passa_para_o_proximo_dentro_do_limite():
    chamadas = []
    inicio = time.monotonic()
    r = cliente_llm.gerar_resposta("p", "c", transporte(
        {"forte": ("lento", 2.0), "medio": ("ok", "do médio")}, chamadas))
    assert r["modelo"] == "medio" and r["texto"] == "do médio"
    assert time.monotonic() - inicio < 1.8  # não esperou o modelo lento terminar
    assert r["tentativas"][0]["resultado"].startswith("tempo_esgotado")


def test_vazio_e_erro_http_seguem_a_fila_e_pensamento_e_removido():
    r = cliente_llm.gerar_resposta("p", "c", transporte(
        {"forte": ("vazio", None), "medio": ("http", 404), "fraco": ("ok", "<think>rascunho</think>Final")}, []))
    assert r["modelo"] == "fraco" and r["texto"] == "Final"
    assert [t["resultado"] for t in r["tentativas"]] == ["resposta_vazia", "http_404", "ok"]


def test_disjuntor_pula_modelo_que_falhou():
    chamadas = []
    comportamento = {"forte": ("http", 503), "medio": ("ok", "ok")}
    cliente_llm.gerar_resposta("p", "c", transporte(comportamento, chamadas))
    chamadas.clear()
    r = cliente_llm.gerar_resposta("p", "c", transporte(comportamento, chamadas))
    assert [c["model"] for c in chamadas] == ["medio"]
    assert r["tentativas"][0] == {"modelo": "forte", "resultado": "pausado", "segundos": 0.0}


def test_fila_esgotada():
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.gerar_resposta("p", "c", transporte({m: ("http", 500) for m in FILA}, []))
    assert [t["modelo"] for t in erro.value.tentativas] == FILA


def test_fila_padrao_tem_cinco_modelos_em_ordem(monkeypatch):
    monkeypatch.delenv("NVIDIA_MODELS")
    assert cliente_llm.fila() == list(cliente_llm.MODELOS_PADRAO)
    assert len(cliente_llm.fila()) == 5 and cliente_llm.fila()[0] == "nvidia/nemotron-3-ultra-550b-a55b"


def test_query_usa_a_fila_e_informa_o_modelo(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")  # o fixture contexto remove as chaves
    _enviar(client)
    monkeypatch.setattr(cliente_llm, "gerar_resposta", lambda p, c: {
        "texto": "Use measure_all [Fonte 1].", "modelo": "medio",
        "tentativas": [{"modelo": "forte", "resultado": "tempo_esgotado_10s", "segundos": 10.0},
                       {"modelo": "medio", "resultado": "ok", "segundos": 2.1}]})
    corpo = client.post("/query", json={"input": "Como medir qubits no Qiskit?"}).json()
    assert corpo["answer_mode"] == "llm" and corpo["llm"]["modelo"] == "medio"
    assert any("após 1 modelo" in a for a in corpo["warnings"])


def test_fila_esgotada_vira_resposta_local(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    _enviar(client)

    def falhar(p, c):
        raise cliente_llm.FilaEsgotada([{"modelo": m, "resultado": "tempo_esgotado_10s", "segundos": 10.0} for m in FILA])
    monkeypatch.setattr(cliente_llm, "gerar_resposta", falhar)
    corpo = client.post("/query", json={"input": "Como medir qubits no Qiskit?"}).json()
    assert corpo["answer_mode"] == "local" and corpo["llm"]["modelo"] is None
    assert any("Nenhum modelo da fila" in a for a in corpo["warnings"])
