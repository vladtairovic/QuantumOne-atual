"""Traço do RAG: etapas, camadas, fallbacks por requisição e contrato da API."""
import contextvars
import json
from pathlib import Path

import pytest

from contratos import RespostaConsulta, ResultadoConsulta
from rag import busca_chromadb, configuracao, pipeline, reranker, traco
from tests.test_integracao import ColecaoTeste

RAIZ = Path(__file__).resolve().parent.parent
CAMADAS = {"reranker", "consulta", "mmr", "orcamento", "verificacao"}


def _meta(doc, secao, titulo):
    return {"document_id": doc, "secao": secao, "indice": 0, "fonte": f"{doc}.md", "titulo": titulo,
            "versao_documentacao": "1", "coletado_em": "2026-01-01"}


@pytest.fixture
def colecao(monkeypatch):
    colecao = ColecaoTeste()
    textos = {
        "bell": ("Circuito de Bell", "Um circuito de Bell usa porta Hadamard e CNOT para emaranhar dois qubits."),
        "ruido": ("Ruido", "O ruido de medicao reduz a fidelidade dos resultados em hardware real."),
    }
    for doc, (titulo, texto) in textos.items():
        colecao.registros[f"{doc}#0"] = (f"{titulo}\n\n{texto}", _meta(doc, titulo, titulo), [1.0, 0.0])
    monkeypatch.setattr(busca_chromadb, "obter_colecao", lambda: colecao)
    monkeypatch.setattr(busca_chromadb, "criar_embeddings", lambda tipo="consulta": lambda t: [[1.0, 0.0] for _ in t])
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("QONE_LLM", "local")
    for nome in CAMADAS:
        monkeypatch.delenv(f"RAG_{nome.upper()}", raising=False)
    monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
    reranker._resetar()
    yield colecao
    reranker._resetar()


PERGUNTA = "circuito de Bell Hadamard CNOT"


def test_trace_tem_todas_as_chaves_e_tipos(colecao):
    r = pipeline.responder(PERGUNTA)
    t = r["trace"]
    assert set(t) == {"etapas", "consultas", "candidatos", "camadas", "modelo_embeddings", "fallbacks"}
    assert isinstance(t["etapas"], dict) and t["etapas"]
    assert all(isinstance(k, str) and isinstance(v, float) for k, v in t["etapas"].items())
    assert "TOTAL" in t["etapas"]
    assert t["consultas"] == [PERGUNTA]
    assert isinstance(t["candidatos"], int) and t["candidatos"] >= 1
    assert t["camadas"] == {nome: False for nome in CAMADAS}
    assert t["modelo_embeddings"] == configuracao.MODELO_EMBEDDINGS
    assert t["fallbacks"] == []


def test_camadas_refletem_as_flags(colecao, monkeypatch):
    monkeypatch.setenv("RAG_MMR", "1")
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    camadas = pipeline.responder(PERGUNTA)["trace"]["camadas"]
    assert camadas == {"reranker": False, "consulta": False, "mmr": True, "orcamento": True, "verificacao": False}


def test_fallback_do_reranker_ausente_aparece_no_trace(colecao, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER", "1")
    t = pipeline.responder(PERGUNTA)["trace"]
    assert t["camadas"]["reranker"] is True
    assert len(t["fallbacks"]) == 1 and "reranker" in t["fallbacks"][0]


def test_insufficient_context_tambem_traz_trace(colecao):
    r = pipeline.responder("zzzz xxxxx yyyyy")
    assert r["status"] == "insufficient_context"
    assert set(r["trace"]) == {"etapas", "consultas", "candidatos", "camadas", "modelo_embeddings", "fallbacks"}


def test_trace_e_isolado_entre_chamadas(colecao, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER", "1")
    primeira = pipeline.responder(PERGUNTA)["trace"]
    assert primeira["fallbacks"]
    monkeypatch.delenv("RAG_RERANKER")
    segunda = pipeline.responder(PERGUNTA)["trace"]
    assert segunda["fallbacks"] == []
    assert primeira["fallbacks"]  # a lista devolvida antes não foi alterada


def test_registrar_fora_de_um_traco_nao_faz_nada():
    def fora():
        traco.registrar_fallback("x")
        return traco.coletar()
    assert contextvars.Context().run(fora) == []


def test_registrar_dentro_de_um_traco_acumula():
    def dentro():
        traco.iniciar()
        traco.registrar_fallback("a")
        traco.registrar_fallback("b")
        return traco.coletar()
    assert contextvars.Context().run(dentro) == ["a", "b"]


def test_openapi_contem_o_schema_do_trace():
    doc = json.loads((RAIZ / "openapi.json").read_text(encoding="utf-8"))
    esquemas = doc["components"]["schemas"]
    assert "TracoRAG" in esquemas
    assert set(esquemas["TracoRAG"]["properties"]) == {
        "etapas", "consultas", "candidatos", "camadas", "modelo_embeddings", "fallbacks"}
    for nome in ("RespostaConsulta", "ResultadoConsulta"):
        if nome in esquemas:
            assert "trace" in esquemas[nome]["properties"], nome
            assert "trace" not in esquemas[nome].get("required", [])
    assert any("trace" in e.get("properties", {}) for e in esquemas.values())


BASE = {"response": "r", "sources": [], "status": "ok"}
TRACE = {"etapas": {"TOTAL": 0.01}, "consultas": ["q"], "candidatos": 2,
         "camadas": {"reranker": False}, "modelo_embeddings": "m", "fallbacks": ["f"]}


def test_modelo_aceita_resposta_com_e_sem_trace():
    assert ResultadoConsulta.model_validate(BASE).trace is None
    com = ResultadoConsulta.model_validate({**BASE, "trace": TRACE})
    assert com.trace.candidatos == 2 and com.trace.fallbacks == ["f"]
    assert com.model_dump(mode="json")["trace"] == TRACE


def test_modelo_aceita_campos_das_camadas_opcionais():
    r = ResultadoConsulta.model_validate({**BASE, "variantes_consulta": ["q", "q2"],
                                          "verificacao": {"fidelidade": 1.0, "frases_sem_suporte": 0}})
    assert r.variantes_consulta == ["q", "q2"] and r.verificacao["fidelidade"] == 1.0
