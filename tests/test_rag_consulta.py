"""Entendimento da consulta: JSON da LLM, fallback local, cache e fusão multi-query."""
import json

import httpx
import pytest

from rag import avaliacao_rag, busca_chromadb, cliente_llm, consulta, pipeline, traco


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.5")
    cliente_llm.limpar_pausas()
    consulta.limpar_cache()
    yield
    cliente_llm.limpar_pausas()
    consulta.limpar_cache()


def _transporte(texto, chamadas=None, status=200):
    def responder(request):
        if chamadas is not None:
            chamadas.append(json.loads(request.content))
        return httpx.Response(status, json={"choices": [{"message": {"content": texto}}]})
    return httpx.MockTransport(responder)


def _proibido(request):
    raise AssertionError("chamada de rede inesperada")


PROIBIDO = httpx.MockTransport(_proibido)
PERGUNTA = "como criar circuito de bell"


def test_json_valido_acrescenta_variantes():
    chamadas = []
    r = consulta.expandir_consulta("pergunta", transporte=_transporte('{"consultas":["a","b"]}', chamadas))
    assert r == ["pergunta", "a", "b"]
    assert chamadas[0]["temperature"] == 0.0 and chamadas[0]["max_tokens"] == 300


def test_cercas_de_codigo_e_texto_ao_redor_sao_toleradas():
    r = consulta.expandir_consulta("pergunta", transporte=_transporte('Claro:\n```json\n{"consultas":["x"]}\n```'))
    assert r == ["pergunta", "x"]


@pytest.mark.parametrize("texto", ["isto não é json", "", '{"consultas": []}', '{"consultas": "a"}',
                                   '{"consultas": [1, 2]}', '{"outra": ["a"]}', "[1,2]"])
def test_resposta_invalida_cai_no_fallback_sem_lancar(texto):
    r = consulta.expandir_consulta(PERGUNTA, transporte=_transporte(texto))
    assert r[0] == PERGUNTA and len(r) == 2
    assert "quantumcircuit" in r[1] and "circuito" in r[1]


def test_fallback_sem_termos_novos_devolve_so_a_original():
    assert consulta.expandir_consulta("xyzzy plugh", transporte=_transporte("lixo")) == ["xyzzy plugh"]


def test_sem_chave_nao_chama_a_rede(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY")
    r = consulta.expandir_consulta(PERGUNTA, transporte=PROIBIDO)
    assert r[0] == PERGUNTA and "quantumcircuit" in r[1]


def test_fila_esgotada_usa_fallback_e_nao_entra_no_cache():
    r = consulta.expandir_consulta(PERGUNTA, transporte=_transporte("falha", status=500))
    assert r[0] == PERGUNTA and len(r) == 2
    # uma falha transitória não pode ficar gravada: a próxima chamada consulta a LLM de novo
    r2 = consulta.expandir_consulta(PERGUNTA, transporte=_transporte('{"consultas":["recuperada"]}'))
    assert r2 == [PERGUNTA, "recuperada"]


def test_erro_inesperado_do_cliente_nao_propaga(monkeypatch):
    def quebrar(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(cliente_llm, "conversar", quebrar)
    assert consulta.expandir_consulta(PERGUNTA)[0] == PERGUNTA


@pytest.mark.parametrize("pergunta", ["", "   ", "```python\nqc.h(0)\n```", "```\n123\n```\n\n```x```"])
def test_pergunta_vazia_ou_so_codigo_nao_chama_llm(pergunta):
    assert consulta.expandir_consulta(pergunta, transporte=PROIBIDO) == [pergunta]


def test_pergunta_de_uma_palavra():
    assert consulta.expandir_consulta("qubit", transporte=_transporte('{"consultas":["qubit basics"]}')) == [
        "qubit", "qubit basics"]
    assert consulta.expandir_consulta("emaranhamento", transporte=_transporte("lixo"))[0] == "emaranhamento"


def test_maximo_de_quatro_sem_duplicatas():
    texto = json.dumps({"consultas": ["Pergunta", "a", " A ", "b", "c", "d", "e", "f", "g", "h"]})
    r = consulta.expandir_consulta("pergunta", transporte=_transporte(texto))
    assert r == ["pergunta", "a", "b", "c"]


def test_cache_por_pergunta_e_conversa_sem_guardar_transporte():
    chamadas = []
    t = _transporte('{"consultas":["a"]}', chamadas)
    consulta.expandir_consulta("p", "c1", transporte=t)
    assert consulta.expandir_consulta("p", "c1", transporte=PROIBIDO) == ["p", "a"]
    assert len(chamadas) == 1
    consulta.expandir_consulta("p", "c2", transporte=t)
    assert len(chamadas) == 2
    assert set(consulta._CACHE) == {("p", "c1"), ("p", "c2")}


def test_conversa_vai_no_prompt():
    chamadas = []
    consulta.expandir_consulta("e o ghz?", "Usuário: o que é bell?",
                               transporte=_transporte('{"consultas":[]}', chamadas))
    assert "o que é bell?" in chamadas[0]["messages"][1]["content"]


# ---- busca multi-query -------------------------------------------------------------------

_TEXTOS = {"d1": "alpha alpha alpha", "d2": "beta beta beta", "d3": "gamma gamma gamma", "d4": "delta delta"}
_VETORES = {"d1": [1.0, 0.0], "d2": [0.0, 1.0], "d3": [-1.0, 0.0], "d4": [0.0, -1.0]}


def _colecao():
    chunks = [{"id": f"{d}#0", "texto": t,
               "metadados": {"document_id": d, "secao": "S", "fonte": "f", "titulo": "t" + d, "indice": 0,
                             "versao_documentacao": "v", "coletado_em": "c"}} for d, t in _TEXTOS.items()]
    return avaliacao_rag.ColecaoMemoria(chunks, [_VETORES[c["metadados"]["document_id"]] for c in chunks])


@pytest.fixture
def busca(monkeypatch):
    colecao = _colecao()
    monkeypatch.setattr(busca_chromadb, "obter_colecao", lambda: colecao)
    # a consulta "beta" aponta para d2; as demais para d1
    monkeypatch.setattr(busca_chromadb, "criar_embeddings", lambda: lambda ts: [
        _VETORES["d2"] if ts[0] == "beta" else _VETORES["d1"] for _ in ts])
    busca_chromadb.invalidar_indice_lexical()
    return colecao


def test_varias_consultas_fundem_ranks_e_guardam_o_melhor(busca):
    r = busca_chromadb.buscar_documentos("alpha", quantidade=4, modo="hibrido", consultas=["alpha", "beta"])
    ids = [d["id"] for d in r]
    assert set(ids[:2]) == {"d1#0", "d2#0"}  # cada consulta contribui com o seu melhor documento
    por_id = {d["id"]: d for d in r}
    assert por_id["d1#0"]["ranks"] == {"vetorial": 1, "lexical": 1}
    assert por_id["d2#0"]["ranks"] == {"vetorial": 1, "lexical": 1}  # melhor rank entre as consultas
    # d1: rank 1 vetorial e lexical em "alpha" + rank 2 vetorial em "beta" (d1 e d3 empatam em distância)
    assert por_id["d1#0"]["pontuacao"] > 2 / 61
    assert por_id["d2#0"]["pontuacao"] > 2 / 61


def test_uma_consulta_ou_nenhuma_e_identico_ao_caminho_antigo(busca):
    kw = dict(quantidade=4, modo="hibrido")
    sem = busca_chromadb.buscar_documentos("alpha", **kw)
    assert busca_chromadb.buscar_documentos("alpha", consultas=None, **kw) == sem
    assert busca_chromadb.buscar_documentos("alpha", consultas=["alpha"], **kw) == sem
    assert busca_chromadb.buscar_documentos("alpha", consultas=[], **kw) == sem
    # aritmética antiga: soma 1/(60+rank) de cada lista
    assert sem[0]["pontuacao"] == round(1 / 61 + 1 / 61, 6)


# ---- pipeline ----------------------------------------------------------------------------

def _doc():
    return [{"id": "d1#0", "texto": "circuito bell", "metadados": {
        "document_id": "d1", "secao": "S", "fonte": "f", "titulo": "t"}}]


@pytest.fixture
def pipe(monkeypatch):
    visto = {}

    def buscar(c, quantidade=3, biblioteca=None, consultas=None, tempos=None):
        visto["consultas"] = consultas
        return _doc()

    monkeypatch.setattr(busca_chromadb, "buscar_documentos", buscar)
    monkeypatch.setattr(pipeline.cliente_llm, "gerar_resposta", lambda *a, **k: {
        "texto": "ok", "modelo": "m", "tentativas": [{"modelo": "m", "resultado": "ok"}]})
    return visto


def test_pipeline_desligado_nao_expande_nem_acrescenta_chave(monkeypatch, pipe):
    monkeypatch.setattr(pipeline, "expandir_consulta", lambda *a, **k: pytest.fail("expandiu"))
    r = pipeline.responder("circuito bell")
    assert pipe["consultas"] is None and "variantes_consulta" not in r


def test_pipeline_ligado_passa_as_variantes(monkeypatch, pipe):
    monkeypatch.setenv("RAG_CONSULTA", "1")
    chamado = {}

    def falso(c, conversa=None, transporte=None):
        chamado.update(c=c, conversa=conversa, transporte=transporte)
        return [c, "bell state circuit"]

    monkeypatch.setattr(pipeline, "expandir_consulta", falso)
    r = pipeline.responder("historico\n\ncircuito bell", consulta="circuito bell")
    assert pipe["consultas"] == ["circuito bell", "bell state circuit"]
    assert r["variantes_consulta"] == ["circuito bell", "bell state circuit"]
    assert chamado == {"c": "circuito bell", "conversa": "historico\n\ncircuito bell", "transporte": None}


def test_pipeline_ligado_sem_llm_nao_expande(monkeypatch, pipe):
    monkeypatch.setenv("RAG_CONSULTA", "1")
    monkeypatch.setenv("QONE_LLM", "local")
    monkeypatch.setattr(pipeline, "expandir_consulta", lambda *a, **k: pytest.fail("expandiu"))
    r = pipeline.responder("circuito bell")
    assert pipe["consultas"] is None and "variantes_consulta" not in r


# ---- prazo curto, fallbacks no traço, cache e conversa -------------------------------------

def _lento(atraso):
    import time

    def responder(request):
        time.sleep(atraso)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"consultas":["tarde"]}'}}]})
    return httpx.MockTransport(responder)


def test_prazo_da_reescrita_esgotado_cai_no_fallback_e_registra(monkeypatch):
    import time
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("RAG_CONSULTA_TIMEOUT", "0.5")
    traco.iniciar()
    inicio = time.monotonic()
    r = consulta.expandir_consulta(PERGUNTA, transporte=_lento(1.5))
    assert time.monotonic() - inicio < 1.5
    assert r[0] == PERGUNTA and "quantumcircuit" in r[1]
    assert any("indisponível (FilaEsgotada)" in f for f in traco.coletar())


def test_prazo_da_reescrita_vai_como_prazo_por_modelo_e_orcamento(monkeypatch):
    visto = {}

    def falso(mensagens, **k):
        visto.update(k)
        return {"texto": '{"consultas":["a"]}'}
    monkeypatch.setattr(cliente_llm, "conversar", falso)
    consulta.expandir_consulta("pergunta um")
    assert visto["modelos"] == cliente_llm.fila_rapida()  # fila rápida: só gera consultas de busca
    assert visto["prazo_por_modelo"] == 4.0 and visto["orcamento_total"] == 4.0
    for bruto, esperado in [("2.5", 2.5), ("0.01", 0.5), ("500", 30.0), ("lixo", 4.0), ("nan", 4.0), ("inf", 4.0),
                            ("", 4.0), ("-3", 0.5)]:
        monkeypatch.setenv("RAG_CONSULTA_TIMEOUT", bruto)
        consulta.limpar_cache()
        consulta.expandir_consulta("pergunta um")
        assert visto["prazo_por_modelo"] == visto["orcamento_total"] == esperado, bruto


def test_resposta_malformada_registra_fallback():
    traco.iniciar()
    consulta.expandir_consulta(PERGUNTA, transporte=_transporte("isto não é json"))
    assert any("malformada" in f for f in traco.coletar())
    traco.iniciar()
    consulta.expandir_consulta(PERGUNTA, transporte=_transporte('{"consultas": []}'))
    assert any("malformada" in f for f in traco.coletar())


def test_sem_llm_configurada_registra_fallback(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY")
    traco.iniciar()
    consulta.expandir_consulta(PERGUNTA, transporte=PROIBIDO)
    assert "consulta: sem LLM configurada" in traco.coletar()


def test_resposta_valida_nao_registra_fallback():
    traco.iniciar()
    consulta.expandir_consulta("pergunta", transporte=_transporte('{"consultas":["a"]}'))
    assert traco.coletar() == []


def test_conversa_longa_entra_no_cache_por_hash_sem_mudar_a_semantica():
    chamadas = []
    t = _transporte('{"consultas":["a"]}', chamadas)
    longa = "x" * 5000
    consulta.expandir_consulta("p", longa, transporte=t)
    assert consulta.expandir_consulta("p", longa, transporte=PROIBIDO) == ["p", "a"]
    assert len(chamadas) == 1
    consulta.expandir_consulta("p", longa + "y", transporte=t)  # outra conversa: outra entrada
    assert len(chamadas) == 2
    assert all(len(c[1] or "") < 100 for c in consulta._CACHE)


def test_pipeline_so_passa_conversa_quando_difere_da_consulta(monkeypatch, pipe):
    monkeypatch.setenv("RAG_CONSULTA", "1")
    chamado = {}

    def falso(c, conversa=None, transporte=None):
        chamado.update(conversa=conversa)
        return [c]
    monkeypatch.setattr(pipeline, "expandir_consulta", falso)
    pipeline.responder("circuito bell")
    assert chamado["conversa"] is None
