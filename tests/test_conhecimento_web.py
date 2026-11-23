import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
import httpx
import pytest
from rag import coleta_web, pipeline_gemini, sincronizar_conhecimento as sync, configuracao, banco
from tests.test_integracao import contexto, TokenizerTeste


@pytest.mark.parametrize("url", [
    "http://quantum.cloud.ibm.com/docs/en/a", "https://localhost/docs/en/a",
    "https://quantum.cloud.ibm.com.evil.test/docs/en/a",
    "https://u:p@quantum.cloud.ibm.com/docs/en/a",
    "https://quantum.cloud.ibm.com:8443/docs/en/a",
    "https://quantum.cloud.ibm.com/docs/en/a?next=http://localhost",
])
def test_coleta_bloqueia_destinos_nao_permitidos(url):
    with pytest.raises(ValueError):
        coleta_web.validar_url(url)


def test_redirect_privado_bloqueado_antes_da_conexao():
    chamadas=[]
    def transporte(req):
        chamadas.append(str(req.url))
        return httpx.Response(302,headers={"location":"http://127.0.0.1/segredo"})
    with httpx.Client(transport=httpx.MockTransport(transporte)) as c:
        with pytest.raises(ValueError):
            coleta_web.baixar(c,"https://quantum.cloud.ibm.com/docs/en/a")
    assert len(chamadas)==1


def test_extracao_remove_navegacao_e_scripts():
    texto="Portas quânticas e medições. "*20
    titulo, secoes=coleta_web.extrair("<nav>menu externo</nav><main><nav>menu</nav><h1>Circuitos</h1><script>segredo</script><h2>Portas</h2><p>"+texto+"</p><pre>h(0)\ncx(0,1)</pre></main>")
    assert titulo=="Circuitos"
    assert "menu" not in json.dumps(secoes) and "segredo" not in json.dumps(secoes)
    assert any("h(0)" in b["texto"] for s in secoes for b in s["blocos"])


def test_sync_incremental_falha_preserva_e_reconstrucao_offline(contexto,monkeypatch,tmp_path):
    _,colecao=contexto
    catalogo=tmp_path/"fontes.json"
    catalogo.write_text(json.dumps([dict(document_id="oficial",url="https://quantum.cloud.ibm.com/docs/en/a",versao="2.6")]))
    monkeypatch.setattr(sync,"CATALOGO",catalogo)
    monkeypatch.setenv("KNOWLEDGE_PATH",str(tmp_path/"snapshots"))
    monkeypatch.setattr(banco,"obter_colecao",lambda:colecao)
    monkeypatch.setattr(configuracao,"obter_modelo",lambda:NS(tokenizer=TokenizerTeste()))
    monkeypatch.setattr(configuracao,"limite_tokens",lambda:110)
    coletar=Mock(return_value=("Circuitos",[dict(titulo="Portas",blocos=[dict(tipo="paragrafo",texto="Portas H e CX no Qiskit.")])]))
    class Coletor:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def coletar(self,url):return coletar(url)
    monkeypatch.setattr(sync,"Coletor",Coletor)
    assert sync.sincronizar()["documents"][0]["status"]=="indexed"
    assert sync.sincronizar()["documents"][0]["status"]=="unchanged"
    anterior=dict(colecao.registros)
    coletar.side_effect=RuntimeError("site indisponível")
    assert sync.sincronizar()["status"]=="partial"
    assert colecao.registros==anterior
    colecao.registros.clear()
    assert sync.sincronizar(True)["status"]=="ok"
    assert colecao.registros==anterior


def test_sync_falha_escrita_nao_marca_indexado(contexto,monkeypatch,tmp_path):
    _,colecao=contexto
    catalogo=tmp_path/"fontes.json"
    catalogo.write_text(json.dumps([dict(document_id="oficial",url="https://quantum.cloud.ibm.com/docs/en/a",versao="2.6")]))
    monkeypatch.setattr(sync,"CATALOGO",catalogo)
    monkeypatch.setenv("KNOWLEDGE_PATH",str(tmp_path/"snapshots"))
    monkeypatch.setattr(banco,"obter_colecao",lambda:colecao)
    monkeypatch.setattr(configuracao,"obter_modelo",lambda:NS(tokenizer=TokenizerTeste()))
    monkeypatch.setattr(configuracao,"limite_tokens",lambda:110)
    d=dict(document_id="oficial",titulo="Portas",fonte="https://quantum.cloud.ibm.com/docs/en/a",
           versao_documentacao="2.6",coletado_em="2026-01-01",secoes=[dict(titulo="Portas",blocos=[dict(tipo="paragrafo",texto="Porta H.")])])
    sync.pasta().mkdir()
    sync.gravar(sync.pasta()/"oficial.json",d)
    colecao.falhar_escrita=True
    assert sync.sincronizar(True)["status"]=="partial"
    assert json.loads((sync.pasta()/"oficial.json").read_text())["indexed"] is False
    colecao.falhar_escrita=False
    assert sync.sincronizar(True)["status"]=="ok"


def test_codigo_com_realce_preserva_tokens_e_indentacao():
    html = '<main><h1>Circuitos</h1><p>' + 'Documentacao oficial. '*20 + '</p><pre><code><span>from</span> qiskit <span>import</span> QuantumCircuit\nif True:\n    qc = QuantumCircuit(2)</code></pre></main>'
    _,secoes=coleta_web.extrair(html)
    codigo=secoes[0]["blocos"][-1]["texto"]
    assert codigo == "from qiskit import QuantumCircuit\nif True:\n    qc = QuantumCircuit(2)"
