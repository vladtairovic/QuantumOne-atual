"""Verificação frase a frase das respostas do RAG."""
import pytest

from rag import busca_chromadb, cliente_llm, pipeline, resposta_local, verificacao

F1 = "O circuito de Bell cria emaranhamento entre dois qubits usando Hadamard e CNOT."
F2 = "Medir o circuito de Bell produz resultados correlacionados entre os qubits."
INVENTADA = "Zorblax quantizou a galáxia de Andrômeda com fótons de banana."
FONTES = [{"content": F1 + " " + F2, "source": "s1", "document_id": "d1"},
          {"content": "Qiskit permite simular circuitos quânticos em computadores clássicos.",
           "source": "s2", "document_id": "d2"}]


def test_frases_literais_das_fontes_tem_fidelidade_total():
    r = verificacao.verificar_resposta(f"{F1} [Fonte 1]\n\n{F2} [Fonte 1]", FONTES)
    assert r["fidelidade"] == 1.0
    assert [f["suportada"] for f in r["frases"]] == [True, True]
    assert r["frases"][0]["fontes"] == [1] and "[Fonte" not in r["frases"][0]["texto"]


def test_frase_inventada_entre_duas_verdadeiras():
    r = verificacao.verificar_resposta(f"{F1}\n\n{INVENTADA}\n\n{F2}", FONTES)
    assert [f["suportada"] for f in r["frases"]] == [True, False, True]
    assert r["fidelidade"] == pytest.approx(2 / 3)
    assert r["texto"] == f"{F1}\n\n{INVENTADA}\n\n{F2}"


def test_citacao_inexistente_nao_e_suportada():
    for marcador in ("[Fonte 9]", "[Fonte 0]"):
        r = verificacao.verificar_resposta(f"{F1} {marcador}", FONTES)
        assert r["frases"][0]["suportada"] is False and r["frases"][0]["suporte"] == 0.0
        assert r["fidelidade"] == 0.0


def test_citacao_valida_e_invalida_usa_a_valida():
    r = verificacao.verificar_resposta(f"{F1} [Fonte 1][Fonte 9]", FONTES)
    assert r["frases"][0]["suportada"] is True


def test_suporte_e_medido_so_nas_fontes_citadas():
    r = verificacao.verificar_resposta(f"{F1} [Fonte 2]", FONTES)
    assert r["frases"][0]["suportada"] is False
    assert verificacao.verificar_resposta(F1, FONTES)["frases"][0]["suportada"] is True  # sem citação: todas


def test_citacao_depois_do_ponto_final():
    r = verificacao.verificar_resposta(f"{F1} [Fonte 1]", FONTES)
    assert r["frases"][0]["fontes"] == [1]
    r = verificacao.verificar_resposta(F1.rstrip(".") + ". [Fonte 1]", FONTES)
    assert r["frases"][0]["fontes"] == [1]


def test_so_bloco_de_codigo_nao_e_avaliado():
    r = verificacao.verificar_resposta("```python\nqc.h(0)  # [Fonte 9]\n```", FONTES)
    assert r["frases"] == [] and r["fidelidade"] == 1.0


@pytest.mark.parametrize("resposta", ["", "   \n ", None])
def test_resposta_vazia(resposta):
    assert verificacao.verificar_resposta(resposta, FONTES)["fidelidade"] == 0.0


def test_sem_fontes_nao_lanca():
    r = verificacao.verificar_resposta(F1, [])
    assert r["fidelidade"] == 0.0 and r["frases"][0]["suporte"] == 0.0
    assert verificacao.verificar_resposta(f"{F1} [Fonte 1]", [])["fidelidade"] == 0.0


def test_cabecalho_e_limitacao_da_resposta_local_nao_contam():
    local = resposta_local.gerar("circuito de bell emaranhamento", [{"texto": F1 + " " + F2}])
    r = verificacao.verificar_resposta(local["response"], [{"content": F1 + " " + F2}])
    assert r["frases"] and r["fidelidade"] == 1.0
    assert all("Resposta extrativa" not in f["texto"] and "Limitação" not in f["texto"] for f in r["frases"])


def test_limiar_explicito_e_por_ambiente(monkeypatch):
    parcial = "Hadamard cria emaranhamento mas Zorblax quantizou bananas."
    base = verificacao.verificar_resposta(parcial, FONTES, limiar=0.0)
    assert base["fidelidade"] == 1.0
    assert verificacao.verificar_resposta(parcial, FONTES, limiar=1.0)["fidelidade"] == 0.0
    monkeypatch.setenv("RAG_LIMIAR_SUPORTE", "1.0")
    assert verificacao.verificar_resposta(parcial, FONTES)["fidelidade"] == 0.0
    monkeypatch.setenv("RAG_LIMIAR_SUPORTE", "lixo")
    assert verificacao._limiar_padrao() == 0.5
    monkeypatch.setenv("RAG_LIMIAR_SUPORTE", "nan")
    assert verificacao._limiar_padrao() == 0.5


# ---- pipeline ---------------------------------------------------------------------------

DOC = [{"id": "d1#0", "texto": F1 + " " + F2, "metadados": {
    "document_id": "d1", "secao": "S", "fonte": "f", "titulo": "t"}}]
PERGUNTA = "circuito de bell emaranhamento"


@pytest.fixture
def pipe(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.delenv("QONE_LLM", raising=False)
    monkeypatch.setattr(busca_chromadb, "buscar_documentos", lambda c, quantidade=3, biblioteca=None, consultas=None, tempos=None: DOC)

    def gerar(texto):
        monkeypatch.setattr(pipeline.cliente_llm, "gerar_resposta", lambda *a, **k: {
            "texto": texto, "modelo": "m", "tentativas": [{"modelo": "m", "resultado": "ok"}]})
    return gerar


def test_pipeline_ligado_descarta_resposta_inventada(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    monkeypatch.setenv("RAG_MIN_FIDELIDADE", "0.8")
    pipe(f"{INVENTADA} [Fonte 1]")
    r = pipeline.responder(PERGUNTA)
    assert r["answer_mode"] == "local" and INVENTADA not in r["response"]
    assert any("descartada" in a and "fontes" in a for a in r["warnings"])
    assert any(INVENTADA[:40] in a for a in r["warnings"])
    assert r["llm"]["modelo"] == "m"
    assert r["verificacao"] == {"fidelidade": 0.0, "frases_sem_suporte": 1}


def test_pipeline_ligado_mantem_resposta_fiel(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    pipe(f"{F1} [Fonte 1]")
    r = pipeline.responder(PERGUNTA)
    assert r["answer_mode"] == "llm" and r["response"] == F1  # texto sem o marcador; a fonte segue em sources
    assert r["verificacao"] == {"fidelidade": 1.0, "frases_sem_suporte": 0}


def test_pipeline_fidelidade_acima_do_minimo_so_avisa(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    monkeypatch.setenv("RAG_MIN_FIDELIDADE", "0.4")
    pipe(f"{F1}\n\n{INVENTADA}\n\n{F2}")
    r = pipeline.responder(PERGUNTA)
    assert r["answer_mode"] == "llm" and INVENTADA in r["response"]
    assert any(INVENTADA[:40] in a for a in r["warnings"])


def test_pipeline_limita_avisos_e_resume(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    monkeypatch.setenv("RAG_MIN_FIDELIDADE", "0")
    pipe("\n\n".join(f"Zorblax{i} quantizou a galáxia número {i} com fótons de banana." for i in range(8)))
    r = pipeline.responder(PERGUNTA)
    assert len(r["warnings"]) == 6 and "8 frases sem suporte nas fontes" in r["warnings"][-1]


def test_pipeline_desligado_nao_toca_na_resposta(monkeypatch, pipe):
    monkeypatch.delenv("RAG_VERIFICACAO", raising=False)
    monkeypatch.setattr(verificacao, "verificar_resposta", lambda *a, **k: pytest.fail("verificou"))
    pipe(f"{INVENTADA} [Fonte 1]")
    r = pipeline.responder(PERGUNTA)
    assert r["answer_mode"] == "llm" and r["response"] == INVENTADA  # texto sem o marcador
    assert "verificacao" not in r and r["warnings"] == []


def test_pipeline_erro_na_verificacao_mantem_resposta_da_llm(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")

    def quebrar(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(verificacao, "verificar_resposta", quebrar)
    pipe(f"{INVENTADA} [Fonte 1]")
    r = pipeline.responder(PERGUNTA)
    assert r["answer_mode"] == "llm" and r["response"] == INVENTADA  # texto sem o marcador
    assert r["verificacao"] == {"erro": True}


def test_pipeline_sem_resposta_local_mantem_insufficient_context(monkeypatch, pipe):
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    monkeypatch.setenv("RAG_MIN_FIDELIDADE", "0.8")
    monkeypatch.setattr(resposta_local, "gerar", lambda *a, **k: None)
    pipe(f"{INVENTADA} [Fonte 1]")
    r = pipeline.responder(PERGUNTA)
    assert r["status"] == "insufficient_context" and r["answer_mode"] == "none"


def test_varredura_de_limiares_escolhe_o_melhor_f1():
    from rag import avaliacao_rag
    tabela, melhor = avaliacao_rag.varrer_limiares([0.9, 0.8, 0.7, 0.4], [0.5, 0.3, 0.2, 0.1])
    assert melhor["limiar"] == 0.35
    assert melhor["precisao"] == 0.8 and melhor["revocacao"] == 1.0 and melhor["f1"] == pytest.approx(0.8889, abs=1e-3)
    assert len(tabela) == 19 and tabela[0]["limiar"] == 0.05


def test_limiar_padrao_documentado(monkeypatch):
    monkeypatch.delenv("RAG_LIMIAR_SUPORTE", raising=False)
    assert verificacao._limiar_padrao() == 0.5
    monkeypatch.setenv("RAG_LIMIAR_SUPORTE", "0.8")
    assert verificacao._limiar_padrao() == 0.8
