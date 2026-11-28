from pathlib import Path
import json

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_integra_servicos_locais_e_banco_persistente_interno():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert set(config["services"]) == {"api", "chroma", "frontend", "cache"}
    assert config["services"]["chroma"]["image"] == "chromadb/chroma:1.5.9"
    assert "ports" not in config["services"]["chroma"]
    assert "chroma_data:/data" in config["services"]["chroma"]["volumes"]
    assert config["networks"]["banco"]["internal"] is True
    assert config["services"]["api"]["environment"]["CHROMA_HOST"] == "chroma"


def test_snapshot_tem_origem_e_secoes_para_reconstrucao():
    docs = json.loads((ROOT / "documentos" / "base_documental.json").read_text(encoding="utf-8"))
    assert len(docs) == 3
    for doc in docs:
        assert doc["document_id"] and doc["secoes"]
        assert doc["fonte"].startswith("https://quantum.cloud.ibm.com/")


def test_codigo_publicado_nao_abre_banco_local():
    for path in (ROOT / "rag").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "PersistentClient(" not in source
        assert "C:/Users/" not in source and "C:\\Users\\" not in source


def test_compose_camadas_padrao_da_implantacao_e_recursos():
    api = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]["api"]
    env = api["environment"]
    assert env["RAG_RERANKER"] == "${RAG_RERANKER:-1}"
    assert env["RAG_RERANKER_MODEL"] == "${RAG_RERANKER_MODEL:-cross-encoder/mmarco-mMiniLMv2-L12-H384-v1}"
    for camada in ("CONSULTA", "ORCAMENTO", "VERIFICACAO"):  # decisão do dono: ligadas no Docker
        assert env[f"RAG_{camada}"] == f"${{RAG_{camada}:-1}}"
    assert env["RAG_MMR"] == "${RAG_MMR:-0}"  # piorou a busca nas medições: desligado
    assert api["mem_limit"] == "${API_MEMORY_LIMIT:-4g}" and api["cpus"] == "${API_CPU_LIMIT:-4}"
    assert env["RAG_RERANKER_TOPO"] == "${RAG_RERANKER_TOPO:-30}" and env["RAG_RERANKER_PESO"] == "${RAG_RERANKER_PESO:-0.7}"
    assert env["RAG_CONSULTA_TIMEOUT"] == "${RAG_CONSULTA_TIMEOUT:-4}"  # reescrita na fila rápida
    assert "no-new-privileges:true" in api["security_opt"]
    assert env["OMP_NUM_THREADS"] == "${OMP_NUM_THREADS:-4}"
    assert api["cap_drop"] == ["ALL"] and api["pids_limit"] == 128
