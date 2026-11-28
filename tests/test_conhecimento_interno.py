"""Prioridade 1: conhecimento interno validado, curadoria e catálogo de fontes."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from qiskit import qasm2

from quantum import migracao
from rag import conhecimento_interno, curadoria
from rag.sincronizar_conhecimento import CATALOGO, curar, validar_catalogo
from tests.test_integracao import ColecaoTeste, TokenizerTeste

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTOS = conhecimento_interno.carregar()
BLOCOS = list(conhecimento_interno.blocos_validaveis(DOCUMENTOS))


def test_documentos_internos_sao_identificados_como_tal():
    assert len(DOCUMENTOS) >= 10
    for d in DOCUMENTOS:
        assert d["origem_conhecimento"] == "qone_interno"
        assert d["fonte"].startswith("qone://conhecimento/")
        assert d["titulo"].startswith("QuantumOne")
        assert d["secoes"] and all(s["blocos"] for s in d["secoes"])


@pytest.mark.parametrize("doc_id,secao,linguagem,codigo", [b for b in BLOCOS if b[2] == "python"],
                         ids=lambda v: v if isinstance(v, str) and len(v) < 60 else None)
def test_exemplos_python_validados_no_qiskit_instalado(doc_id, secao, linguagem, codigo, tmp_path):
    script = tmp_path / "exemplo.py"
    script.write_text(codigo, encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=120,
                       cwd=tmp_path)
    assert r.returncode == 0, f"{doc_id} / {secao}\n{r.stderr[-1500:]}"


@pytest.mark.parametrize("doc_id,secao,linguagem,codigo", [b for b in BLOCOS if b[2] == "qasm"])
def test_exemplos_qasm_validados(doc_id, secao, linguagem, codigo):
    qasm2.loads(codigo)


def test_ha_exemplos_validaveis_em_quase_todos_os_documentos():
    com_validacao = {b[0] for b in BLOCOS}
    assert len(BLOCOS) >= 20
    assert len(com_validacao) >= len(DOCUMENTOS) - 1


@pytest.mark.parametrize("regra", migracao.REGRAS, ids=lambda r: r["id"])
def test_regras_de_migracao_conferem_com_qiskit_instalado(regra):
    if regra.get("ausente"):
        assert not migracao.existe(regra["ausente"]), f"{regra['antigo']} ainda existe"
    if regra.get("presente"):
        assert migracao.existe(regra["presente"]), f"substituto {regra['novo']} não existe"


def test_curadoria_marca_api_removida_mas_nao_guia_de_migracao():
    codigo = "from qiskit import execute, Aer\nqc.cnot(0, 1)"
    marcado = curadoria.detectar_desatualizado(codigo, "Tutorial antigo")
    assert marcado["desatualizado"] and len(marcado["motivos"]) == 3
    assert not curadoria.detectar_desatualizado(codigo, "Qiskit 1.0 migration guide")["desatualizado"]
    assert not curadoria.detectar_desatualizado("qc.cx(0, 1)\nfrom qiskit_aer import AerSimulator")["desatualizado"]


def test_curadoria_remove_duplicados_e_boilerplate_preservando_codigo():
    rodape = {"tipo": "paragrafo", "texto": "Was this page helpful? Report a bug."}
    codigo = {"tipo": "codigo", "texto": "qc.h(0)"}
    secoes = [{"titulo": "A", "blocos": [rodape, {"tipo": "paragrafo", "texto": "Texto único."}, codigo, codigo]}]
    docs = [{"secoes": [{"titulo": "x", "blocos": [rodape]}]} for _ in range(3)]
    boiler = curadoria.boilerplate_entre_documentos(docs)
    limpas, removidos = curadoria.remover_duplicados(secoes, boiler)
    textos = [b["texto"] for b in limpas[0]["blocos"]]
    assert textos == ["Texto único.", "qc.h(0)"] and removidos == 2


def test_curar_inclui_classificacao_do_catalogo():
    fonte = {"document_id": "x", "url": "https://openqasm.com/language/gates.html", "versao": "3.0",
             "biblioteca": "openqasm", "temas": ["openqasm3", "portas"], "nivel": "intermediario",
             "licenca": "Apache-2.0"}
    d = curar(fonte, "Gates", [{"titulo": "1. Gates", "blocos": [{"tipo": "paragrafo", "texto": "gate h a { U(pi/2,0,pi) a; }"}]}],
              fonte["url"])
    assert d["biblioteca"] == "openqasm" and d["temas"] == "openqasm3,portas" and d["desatualizado"] is False


def test_indexacao_do_conhecimento_interno_e_incremental(monkeypatch):
    colecao = ColecaoTeste()
    modelo = SimpleNamespace(tokenizer=TokenizerTeste(), max_seq_length=128)
    from rag import configuracao, indexar_documentos
    monkeypatch.setattr(configuracao, "obter_modelo", lambda: modelo)
    monkeypatch.setattr(configuracao, "limite_tokens", lambda: 110)
    monkeypatch.setattr(indexar_documentos, "criar_embeddings", lambda tipo="consulta": lambda textos: [[1.0, 0.0] for _ in textos])
    primeiro = conhecimento_interno.indexar(colecao)
    assert primeiro == {"indexados": len(DOCUMENTOS), "inalterados": 0}
    metas = [m for _, m, _ in colecao.registros.values()]
    assert all(m["origem_conhecimento"] == "qone_interno" and m["biblioteca"] for m in metas)
    assert conhecimento_interno.indexar(colecao) == {"indexados": 0, "inalterados": len(DOCUMENTOS)}


def test_catalogo_de_fontes_valido_e_classificado():
    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))
    validar_catalogo(catalogo)
    assert len(catalogo) >= 150
    for fonte in catalogo:
        assert fonte["licenca"] and fonte["biblioteca"] and fonte["temas"] and fonte["tipo_fonte"]
    bibliotecas = {f["biblioteca"] for f in catalogo}
    # PennyLane fica fora: o site responde 403 ao robots.txt para coletores automatizados.
    assert {"qiskit", "qiskit_aer", "openqasm", "cirq", "qsharp", "braket", "qutip", "geral"} <= bibliotecas
