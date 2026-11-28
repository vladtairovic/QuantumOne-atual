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
    assert set(chamadas[0]) == {"model", "messages", "max_tokens", "temperature", "stream"} and chamadas[0]["stream"] is True
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
    comportamento = {"forte": ("http", 404), "medio": ("ok", "ok")}  # só erros permanentes pausam
    cliente_llm.gerar_resposta("p", "c", transporte(comportamento, chamadas))
    chamadas.clear()
    r = cliente_llm.gerar_resposta("p", "c", transporte(comportamento, chamadas))
    assert [c["model"] for c in chamadas] == ["medio"]
    assert r["tentativas"][0] == {"modelo": "forte", "resultado": "pausado", "segundos": 0.0}


def test_fila_esgotada():
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.gerar_resposta("p", "c", transporte({m: ("http", 500) for m in FILA}, []))
    assert [t["modelo"] for t in erro.value.tentativas] == FILA


def test_fila_padrao_tem_sete_modelos_em_ordem(monkeypatch):
    monkeypatch.delenv("NVIDIA_MODELS")
    assert cliente_llm.fila() == list(cliente_llm.MODELOS_PADRAO)
    assert cliente_llm.fila() == [
        "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "poolside/laguna-xs-2.1", "nvidia/nemotron-3.5-bolt-e4b", "nvidia/ising-calibration-1.5-31b"]


def test_limite_padrao_e_45_segundos(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS")
    import inspect
    assert 'getenv("LLM_TIMEOUT_SECONDS", "45")' in inspect.getsource(cliente_llm.conversar)


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


def _sse(pedacos, atraso_apos=None, atraso=0.0):
    """Corpo SSE no formato do chat/completions; ``atraso_apos`` pausa depois desse pedaço."""
    def gerar():
        for n, pedaco in enumerate(pedacos):
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": pedaco}}]}) + "\n\n").encode()
            if atraso_apos is not None and n == atraso_apos:
                time.sleep(atraso)
        yield b"data: [DONE]\n\n"
    return gerar()


def test_streaming_junta_os_pedacos_remove_raciocinio_e_avisa_o_parcial(monkeypatch):
    monkeypatch.setattr(cliente_llm, "_INTERVALO_PARCIAL", 0)
    parciais = []

    def responder(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=_sse(["<think>pensando", "</think>Olá", ", mundo", " [Fonte 1]"]))

    r = cliente_llm.conversar([{"role": "user", "content": "oi"}], transporte=httpx.MockTransport(responder),
                              ao_parcial=lambda modelo, texto: parciais.append((modelo, texto)))
    assert r["modelo"] == "forte" and r["texto"] == "Olá, mundo [Fonte 1]"
    assert parciais[0] == ("forte", "") and parciais[-1] == ("forte", "Olá, mundo [Fonte 1]")
    assert all("pensando" not in texto for _, texto in parciais)


def test_parcial_de_modelo_que_estourou_o_prazo_nao_vaza_para_o_proximo(monkeypatch):
    monkeypatch.setattr(cliente_llm, "_INTERVALO_PARCIAL", 0)
    eventos = []

    def responder(request):
        modelo = json.loads(request.content)["model"]
        if modelo == "forte":  # escreve um pedaço e trava além do prazo de 0,5 s
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse(["começo do forte", " fim tardio do forte"], atraso_apos=0, atraso=1.2))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(["resposta do médio"]))

    r = cliente_llm.conversar([{"role": "user", "content": "oi"}], transporte=httpx.MockTransport(responder),
                              ao_progresso=lambda modelo, *a: eventos.append(("modelo", modelo)),
                              ao_parcial=lambda modelo, texto: eventos.append(("parcial", modelo, texto)))
    assert r["modelo"] == "medio" and r["tentativas"][0]["resultado"].startswith("tempo_esgotado")
    time.sleep(1.0)  # dá tempo de a thread do modelo lento ler o pedaço tardio
    depois_do_medio = eventos[eventos.index(("modelo", "medio")):]
    assert not [e for e in depois_do_medio if e[0] == "parcial" and e[1] == "forte"]
    assert ("parcial", "forte", "começo do forte") in eventos


def test_progresso_guarda_parcial_so_do_modelo_em_uso():
    from uuid import uuid4

    from rag import progresso
    pid = str(uuid4())
    r = progresso.Rastreador(pid)
    r.modelo("m1", 1, 7, "Redigindo a resposta")
    r.parcial("m1", "texto do m1")
    assert progresso.obter(pid)["parcial"] == "texto do m1"
    r.modelo("m2", 2, 7, "Redigindo a resposta")
    assert progresso.obter(pid)["parcial"] is None  # trocou de modelo: o texto anterior some
    r.parcial("m1", "atrasado")
    assert progresso.obter(pid)["parcial"] is None
    r.registrar("Internet: 3 resultado(s)")
    estado = progresso.obter(pid)
    assert estado["etapa"].endswith("(2/7)") and estado["historico"][-1]["texto"] == "Internet: 3 resultado(s)"
    r.concluir()
    assert progresso.obter(pid)["parcial"] is None


# ---- prazo_por_modelo / orcamento_total opcionais ------------------------------------------

def _lento(atraso):
    def responder(request):
        time.sleep(atraso)
        return httpx.Response(200, json={"choices": [{"message": {"content": "tarde"}}]})
    return httpx.MockTransport(responder)


def test_orcamento_total_explicito_encerra_cedo_com_fila_esgotada(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
    inicio = time.monotonic()
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.conversar([{"role": "user", "content": "oi"}], modelos=FILA, transporte=_lento(1.5),
                              orcamento_total=0.5, prazo_por_modelo=0.5)
    assert time.monotonic() - inicio < 1.2
    assert erro.value.tentativas[0]["resultado"].startswith("tempo_esgotado")
    assert [t["resultado"] for t in erro.value.tentativas[1:]] == ["sem_tempo", "sem_tempo"]


def test_prazo_por_modelo_nunca_ultrapassa_o_limite_do_ambiente(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.3")
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.conversar([{"role": "user", "content": "oi"}], modelos=["forte"], transporte=_lento(1.0),
                              prazo_por_modelo=20)
    assert erro.value.tentativas[0]["resultado"] == "tempo_esgotado_0.3s"


def test_sem_parametros_novos_o_teto_total_continua_vindo_do_ambiente(monkeypatch):
    monkeypatch.setenv("LLM_TEMPO_TOTAL_SECONDS", "0.5")  # <= 1 s de sobra: nenhum modelo é tentado (comportamento antigo)
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.conversar([{"role": "user", "content": "oi"}], modelos=["forte"],
                              transporte=transporte({"forte": ("ok", "x")}, []))
    assert erro.value.tentativas[0]["resultado"] == "sem_tempo"


# ---- insistência: a fila é percorrida de novo até um modelo responder -------------------------

def test_fila_insiste_em_nova_rodada_ate_responder():
    chamadas, avisos, esperas = [], [], []
    rodada = {"n": 0}

    def responder(request):
        corpo = json.loads(request.content)
        chamadas.append(corpo["model"])
        if corpo["model"] == FILA[0]:
            rodada["n"] += 1
        if rodada["n"] < 3:  # as duas primeiras rodadas falham inteiras
            return httpx.Response(503, json={"detail": "instável"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "enfim"}}]})

    r = cliente_llm.conversar_insistente([{"role": "user", "content": "x"}], transporte=httpx.MockTransport(responder),
                                         ao_aviso=avisos.append, espera=esperas.append)
    assert r["texto"] == "enfim" and r["rodadas"] == 3 and r["modelo"] == FILA[0]
    assert esperas == [5, 10]  # pausas crescentes entre as rodadas
    assert "rodada 2" in avisos[0] and "rodada 3" in avisos[1]
    assert len(r["tentativas"]) == 2 * len(FILA) + 1  # todas as tentativas ficam registradas


def test_fila_nao_insiste_quando_o_erro_e_permanente():
    esperas = []
    t = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "chave inválida"}))
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.conversar_insistente([{"role": "user", "content": "x"}], transporte=t, espera=esperas.append)
    assert esperas == [] and erro.value.rodadas == 1  # chave inválida: nova rodada não resolveria


def test_fila_para_de_insistir_no_prazo_final():
    esperas = []
    t = httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "fora"}))
    with pytest.raises(cliente_llm.FilaEsgotada) as erro:
        cliente_llm.conversar_insistente([{"role": "user", "content": "x"}], transporte=t, espera=esperas.append,
                                         prazo_final=time.monotonic() + 3)
    assert esperas == [] and erro.value.rodadas == 1  # a próxima pausa já passaria do prazo
