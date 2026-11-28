"""Embeddings configuráveis: prefixos, limite de tokens, nome/estratégia da coleção."""
import re

import pytest

from rag import banco, configuracao, indexar_documentos
from rag.preparar_chunks import gerar_chunks

LEGADO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
E5 = "intfloat/multilingual-e5-base"


class ModeloFalso:
    def __init__(self, max_seq_length):
        self.max_seq_length = max_seq_length


def test_prefixos_e5_e_legado():
    assert configuracao.prefixo("consulta", E5) == "query: "
    assert configuracao.prefixo("passagem", E5) == "passage: "
    assert configuracao.prefixo("consulta", "Intfloat/Multilingual-E5-Small") == "query: "
    assert configuracao.prefixo("consulta", LEGADO) == ""
    assert configuracao.prefixo("passagem", LEGADO) == ""


def test_nome_colecao_legado_e_versionado(monkeypatch):
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    assert configuracao.nome_colecao(LEGADO, 110) == "qiskit_documentacao_multilingue"
    novo = configuracao.nome_colecao(E5, 256)
    assert novo != "qiskit_documentacao_multilingue"
    assert novo == "qone_rag_intfloat_multilingual_e5_base_256"
    # Outro modelo ou outro limite nunca reutiliza o nome.
    assert configuracao.nome_colecao(E5, 110) != novo
    assert configuracao.nome_colecao(LEGADO, 256) not in (novo, "qiskit_documentacao_multilingue")


@pytest.mark.parametrize("modelo", [
    LEGADO, E5, "org/" + "modelo-muito-longo-" * 8, "a/" + "x" * 300, "A.B/modelo_v2.final"])
@pytest.mark.parametrize("limite", [64, 110, 256, 512])
def test_nome_colecao_valido_para_chroma(monkeypatch, modelo, limite):
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    nome = configuracao.nome_colecao(modelo, limite)
    assert 3 <= len(nome) <= 63
    assert re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]", nome)
    assert nome == configuracao.nome_colecao(modelo, limite)  # estável


def test_nome_colecao_longo_nao_colide(monkeypatch):
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    longo = "org/" + "modelo-muito-longo-" * 8
    nomes = {configuracao.nome_colecao(longo, 256), configuracao.nome_colecao(longo + "b", 256),
             configuracao.nome_colecao(longo, 512)}
    assert len(nomes) == 3


def test_nome_colecao_respeita_variavel_explicita(monkeypatch):
    monkeypatch.setenv("CHROMA_COLLECTION", "minha_colecao")
    assert configuracao.nome_colecao(E5, 256) == "minha_colecao"
    assert configuracao.nome_colecao(LEGADO, 110) == "minha_colecao"


def test_estrategia_chunks():
    assert configuracao.estrategia_chunks(110) == "secoes_tokens_v2"
    assert configuracao.estrategia_chunks(256) == "secoes_tokens_v2_256"


def test_limite_tokens_configuravel_e_limitado_pelo_modelo(monkeypatch):
    monkeypatch.delenv("RAG_CHUNK_TOKENS", raising=False)
    assert configuracao.limite_tokens(ModeloFalso(512)) == 110
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "256")
    assert configuracao.limite_tokens(ModeloFalso(512)) == 256
    assert configuracao.limite_tokens(ModeloFalso(128)) == 128


def _chunk(tokens, modelo=LEGADO):
    return {"id": "d1_chunk_0", "texto": "texto", "metadados": {
        "document_id": "d1", "secao": "s", "titulo": "t", "fonte": "f",
        "versao_documentacao": "v", "coletado_em": "c", "indice": 0,
        "tokens": tokens, "modelo_embeddings": modelo}}


def test_validar_lote_usa_limite_configurado(monkeypatch):
    monkeypatch.setattr(configuracao, "limite_tokens", lambda modelo=None: 256)
    assert "d1" in indexar_documentos.validar_lote([_chunk(200)], LEGADO)
    with pytest.raises(ValueError, match="tokens"):
        indexar_documentos.validar_lote([_chunk(300)], LEGADO)


class Tokenizer:
    def encode(self, texto, add_special_tokens=True, truncation=False):
        return texto.split()


DOCUMENTO = {"document_id": "d1", "titulo": "T", "fonte": "f", "versao_documentacao": "v",
             "coletado_em": "c", "secoes": [{"titulo": "S", "blocos": [{"tipo": "texto", "texto": "um dois tres"}]}]}


def test_gerar_chunks_grava_estrategia():
    padrao = gerar_chunks(DOCUMENTO, Tokenizer(), 110, LEGADO)
    assert padrao[0]["metadados"]["estrategia_chunks"] == "secoes_tokens_v2"
    novo = gerar_chunks(DOCUMENTO, Tokenizer(), 256, E5, estrategia="secoes_tokens_v2_256")
    assert novo[0]["metadados"]["estrategia_chunks"] == "secoes_tokens_v2_256"


def test_criar_embeddings_aplica_prefixo(monkeypatch):
    recebidos = []

    class Vetor:
        def tolist(self):
            return [[1.0]]

    class Modelo:
        def encode(self, textos, **kw):
            recebidos.append(list(textos))
            return Vetor()

    monkeypatch.setattr(configuracao, "MODELO_EMBEDDINGS", E5)
    monkeypatch.setattr(configuracao, "obter_modelo", lambda: Modelo())
    monkeypatch.setattr("rag.cache.recuperar", lambda textos, modelo: None)
    salvos = []
    monkeypatch.setattr("rag.cache.salvar", lambda textos, modelo, vetor: salvos.append(list(textos)))
    configuracao.criar_embeddings("consulta")(["pergunta"])
    configuracao.criar_embeddings("passagem")(["trecho"])
    configuracao.criar_embeddings()(["outra"])
    assert recebidos == [["query: pergunta"], ["passage: trecho"], ["query: outra"]]
    # A chave de cache usa o texto já prefixado: consulta e passagem não colidem.
    assert salvos[0] != ["passage: pergunta"]


def test_criar_embeddings_legado_nao_prefixa(monkeypatch):
    recebidos = []

    class Modelo:
        def encode(self, textos, **kw):
            recebidos.append(list(textos))

            class V:
                def tolist(self):
                    return [[1.0]]
            return V()

    monkeypatch.setattr(configuracao, "MODELO_EMBEDDINGS", LEGADO)
    monkeypatch.setattr(configuracao, "obter_modelo", lambda: Modelo())
    monkeypatch.setattr("rag.cache.recuperar", lambda textos, modelo: None)
    monkeypatch.setattr("rag.cache.salvar", lambda *a: None)
    configuracao.criar_embeddings("passagem")(["trecho"])
    assert recebidos == [["trecho"]]


class ColecaoFalsa:
    def __init__(self, metadata, space="l2"):
        self.metadata = metadata
        self.configuration = {"hnsw": {"space": space}}


class ClienteFalso:
    def __init__(self, colecao):
        self.colecao = colecao
        self.nomes = []

    def get_or_create_collection(self, name, **kw):
        self.nomes.append(name)
        return self.colecao


def _esperado():
    return {"modelo_embeddings": configuracao.MODELO_EMBEDDINGS, "normalizado": True,
            "estrategia_chunks": configuracao.estrategia_chunks(configuracao.LIMITE_CONFIGURADO)}


def _obter(monkeypatch, colecao):
    cliente = ClienteFalso(colecao)
    monkeypatch.setattr(banco, "obter_cliente", lambda: cliente)
    banco.obter_colecao.cache_clear()
    try:
        return banco.obter_colecao(), cliente
    finally:
        banco.obter_colecao.cache_clear()


def test_colecao_compativel_usa_nome_de_configuracao(monkeypatch):
    colecao = ColecaoFalsa(_esperado())
    obtida, cliente = _obter(monkeypatch, colecao)
    assert obtida is colecao
    assert cliente.nomes == [configuracao.NOME_COLECAO]


@pytest.mark.parametrize("campo,valor", [
    ("modelo_embeddings", E5), ("estrategia_chunks", "secoes_tokens_v2_999"), ("normalizado", False)])
def test_colecao_de_outro_modelo_ou_estrategia_e_incompativel(monkeypatch, campo, valor):
    with pytest.raises(banco.ColecaoIncompativel):
        _obter(monkeypatch, ColecaoFalsa({**_esperado(), campo: valor}))


def test_colecao_de_outro_espaco_e_incompativel(monkeypatch):
    with pytest.raises(banco.ColecaoIncompativel):
        _obter(monkeypatch, ColecaoFalsa(_esperado(), space="cosine"))


class ColecaoNomeada:
    def __init__(self, nome, metadata):
        self.nome, self.metadata = nome, metadata
        self.configuration = {"hnsw": {"space": "l2"}}
        self.escritas = []
        self.registros = {}

    def get(self, where=None, **kw):
        return {"ids": [i for i, m in self.registros.items()
                        if not where or m == where.get("document_id")]}

    def upsert(self, ids, documents, metadatas, embeddings):
        self.escritas.append(("upsert", list(ids)))
        for i, m in zip(ids, metadatas):
            self.registros[i] = m["document_id"]

    def delete(self, ids):
        self.escritas.append(("delete", list(ids)))
        for i in ids:
            self.registros.pop(i, None)


class ClienteDuasColecoes:
    """Cliente que guarda coleções por nome, como o servidor real."""
    def __init__(self, colecoes):
        self.colecoes = {c.nome: c for c in colecoes}
        self.pedidos = []

    def get_or_create_collection(self, name, **kw):
        self.pedidos.append(name)
        if name not in self.colecoes:
            self.colecoes[name] = ColecaoNomeada(name, kw["metadata"])
        return self.colecoes[name]


def _meta(modelo, estrategia):
    return {"modelo_embeddings": modelo, "normalizado": True, "estrategia_chunks": estrategia}


def _duas_colecoes(monkeypatch, modelo_ativo, nome_ativo):
    legada = ColecaoNomeada("qiskit_documentacao_multilingue", _meta(LEGADO, "secoes_tokens_v2"))
    nova = ColecaoNomeada("qone_rag_e5_256", _meta(E5, "secoes_tokens_v2_256"))
    cliente = ClienteDuasColecoes([legada, nova])
    monkeypatch.setattr(banco, "obter_cliente", lambda: cliente)
    monkeypatch.setattr(banco, "MODELO_EMBEDDINGS", modelo_ativo)
    monkeypatch.setattr(banco, "NOME_COLECAO", nome_ativo)
    monkeypatch.setattr(banco, "LIMITE_CONFIGURADO", 256 if modelo_ativo == E5 else 110)
    monkeypatch.setattr(configuracao, "MODELO_EMBEDDINGS", modelo_ativo)
    monkeypatch.setattr(indexar_documentos, "MODELO_EMBEDDINGS", modelo_ativo)
    monkeypatch.setattr(configuracao, "limite_tokens", lambda modelo=None: 256)
    monkeypatch.setattr(indexar_documentos, "criar_embeddings",
                        lambda tipo="consulta": lambda textos: [[1.0, 0.0] for _ in textos])
    banco.obter_colecao.cache_clear()
    return legada, nova, cliente


def _chunk_e5():
    c = _chunk(200, E5)
    c["metadados"]["estrategia_chunks"] = "secoes_tokens_v2_256"
    return c


def test_duas_colecoes_coexistem_sem_misturar(monkeypatch):
    legada, nova, cliente = _duas_colecoes(monkeypatch, E5, "qone_rag_e5_256")
    try:
        colecao = banco.obter_colecao()
        assert colecao is nova
        assert cliente.pedidos == ["qone_rag_e5_256"]
        indexar_documentos.atualizar_documento(colecao, "d1", [_chunk_e5()])
    finally:
        banco.obter_colecao.cache_clear()
    assert nova.escritas and nova.registros == {"d1_chunk_0": "d1"}
    assert legada.escritas == [] and legada.registros == {}


def test_modelo_e5_apontado_para_colecao_legada_e_incompativel(monkeypatch):
    legada, nova, cliente = _duas_colecoes(monkeypatch, E5, "qiskit_documentacao_multilingue")
    try:
        with pytest.raises(banco.ColecaoIncompativel):
            banco.obter_colecao()
    finally:
        banco.obter_colecao.cache_clear()
    assert legada.escritas == [] and nova.escritas == []


def test_modelo_legado_apontado_para_colecao_e5_e_incompativel(monkeypatch):
    legada, nova, cliente = _duas_colecoes(monkeypatch, LEGADO, "qone_rag_e5_256")
    try:
        with pytest.raises(banco.ColecaoIncompativel):
            banco.obter_colecao()
    finally:
        banco.obter_colecao.cache_clear()
    assert legada.escritas == [] and nova.escritas == []


# ---- RAG_CHUNK_TOKENS validado -----------------------------------------------------------

@pytest.mark.parametrize("valor,esperado", [
    ("garbage", 110), ("0", 110), ("-5", 110), ("15", 110), ("nan", 110), ("inf", 110), ("12.5", 110),
    ("", 110), ("16", 16), ("256", 256), ("  300 ", 300)])
def test_chunk_tokens_invalido_cai_no_padrao(monkeypatch, valor, esperado):
    monkeypatch.setenv("RAG_CHUNK_TOKENS", valor)
    assert configuracao._limite_configurado() == esperado


def test_chunk_tokens_ausente_e_padrao(monkeypatch):
    monkeypatch.delenv("RAG_CHUNK_TOKENS", raising=False)
    assert configuracao._limite_configurado() == 110


def test_chunk_tokens_abaixo_do_minimo_avisa(monkeypatch, caplog):
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "15")
    with caplog.at_level("WARNING"):
        configuracao._limite_configurado()
    assert any("RAG_CHUNK_TOKENS" in r.getMessage() for r in caplog.records)


def test_importar_configuracao_com_valor_invalido_nao_levanta():
    import subprocess, sys
    env = {**__import__("os").environ, "RAG_CHUNK_TOKENS": "lixo", "HF_HUB_OFFLINE": "1"}
    r = subprocess.run([sys.executable, "-c", "from rag import configuracao; print(configuracao.LIMITE_CONFIGURADO)"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "110"
