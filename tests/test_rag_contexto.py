"""MMR (diversidade) e orçamento de contexto proporcional à relevância."""
import pytest

from rag import avaliacao_rag, busca_chromadb, diversidade
from rag.contexto import preparar_contexto


# ---- mmr ---------------------------------------------------------------------------------

def test_mmr_prefere_o_distante_ao_quase_duplicado():
    itens = [("a", [1.0, 0.0], 1.0), ("b", [0.99, 0.1], 0.9), ("c", [0.0, 1.0], 0.5)]
    assert diversidade.mmr(itens, lambda_=0.5, k=2) == ["a", "c"]


def test_mmr_diversidade_exata_com_quase_duplicado_levemente_melhor():
    itens = [("a", [1.0, 0.0], 1.0), ("dup", [1.0, 0.01], 0.95), ("longe", [0.0, 1.0], 0.9)]
    assert diversidade.mmr(itens, lambda_=0.5, k=2) == ["a", "longe"]
    assert diversidade.mmr(itens, lambda_=1.0, k=3) == ["a", "dup", "longe"]
    assert diversidade.mmr(itens, lambda_=0.0, k=3) == ["a", "longe", "dup"]


def test_mmr_lambda_um_e_ordenacao_por_nota_estavel():
    itens = [("a", [1.0, 0.0], 0.5), ("b", [1.0, 0.0], 0.9), ("c", [0.0, 1.0], 0.5), ("d", [1.0, 0.1], 0.9)]
    assert diversidade.mmr(itens, lambda_=1.0, k=4) == ["b", "d", "a", "c"]


def test_mmr_k_maior_que_itens_devolve_todos_e_k_nao_positivo_nada():
    itens = [("a", [1.0, 0.0], 1.0), ("b", [0.0, 1.0], 0.5)]
    assert sorted(diversidade.mmr(itens, k=10)) == ["a", "b"]
    assert diversidade.mmr(itens, k=0) == []
    assert diversidade.mmr(itens, k=-1) == []
    assert diversidade.mmr([], k=3) == []


def test_mmr_notas_iguais_vetor_nulo_e_empates_deterministicos():
    itens = [("a", [0.0, 0.0], 1.0), ("b", [0.0, 0.0], 1.0), ("c", [1.0, 0.0], 1.0)]
    assert diversidade.mmr(itens, k=3) == ["a", "b", "c"]


def test_mmr_dimensoes_diferentes_levantam_value_error():
    with pytest.raises(ValueError):
        diversidade.mmr([("a", [1.0, 0.0], 1.0), ("b", [1.0], 0.5)])


# ---- orçamento de contexto -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _orcamento_desligado(monkeypatch):
    monkeypatch.delenv("RAG_ORCAMENTO", raising=False)


def _doc(n, tamanho=5000, pontuacao=None, texto=None):
    doc = {"texto": texto if texto is not None else "x" * tamanho,
           "metadados": {"titulo": f"T{n}", "secao": f"S{n}", "fonte": f"u{n}", "document_id": f"d{n}"}}
    if pontuacao is not None:
        doc["pontuacao"] = pontuacao
    return doc


def test_tres_documentos_cabem_todos_no_orcamento_dividido(monkeypatch):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    texto, fontes = preparar_contexto([_doc(1), _doc(2), _doc(3)], limite=6000)
    assert [f["id_documento"] for f in fontes] == ["d1", "d2", "d3"]
    assert len(texto) <= 6000


def test_orcamento_proporcional_a_pontuacao_com_piso_de_40_por_cento_no_primeiro(monkeypatch):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    docs = [_doc(1, pontuacao=0.1), _doc(2, pontuacao=0.5), _doc(3, pontuacao=0.4)]
    texto, fontes = preparar_contexto(docs, limite=6000)
    assert len(fontes) == 3 and len(texto) <= 6000
    blocos = texto.split("\n\n[Fonte ")
    tamanhos = [len(b) for b in blocos]
    assert tamanhos[0] >= 0.4 * 6000 - 20  # primeiro nunca abaixo de 40% (descontado o separador)
    assert tamanhos[1] > tamanhos[2]  # pontuação maior, fatia maior


def test_sobra_de_documento_curto_vai_para_os_demais(monkeypatch):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    docs = [_doc(1, 5000), _doc(2, texto="y" * 200), _doc(3, 5000)]
    texto, fontes = preparar_contexto(docs, limite=6000)
    assert len(fontes) == 3 and len(texto) <= 6000
    assert "y" * 200 in texto
    assert texto.count("x") > 4500  # as sobras do curto foram redistribuídas


def test_sem_flag_o_preenchimento_e_sequencial():
    texto, fontes = preparar_contexto([_doc(1), _doc(2), _doc(3)], limite=6000)
    # Como no commit base: o primeiro entra inteiro (5000 chars) e o segundo recebe só o que sobra.
    assert [f["id_documento"] for f in fontes] == ["d1", "d2"]
    assert "x" * 5000 in texto.split("[Fonte 2]")[0]
    assert len(texto) == 6000 - 2


def test_primeiro_documento_consome_o_orcamento_sem_flag():
    texto, fontes = preparar_contexto([_doc(1), _doc(2)], limite=3000)
    assert [f["id_documento"] for f in fontes] == ["d1"] and len(texto) == 3000 - 2


@pytest.mark.parametrize("flag", [None, "1"])
def test_sem_truncamento_a_saida_e_identica_a_sequencial(monkeypatch, flag):
    if flag:
        monkeypatch.setenv("RAG_ORCAMENTO", flag)
    docs = [_doc(1, 300, pontuacao=0.9), _doc(2, 300, pontuacao=0.1), _doc(3, 300)]
    texto, fontes = preparar_contexto(docs, limite=12000)
    esperado = "\n\n".join(
        f"[Fonte {n}]\nTítulo: T{n}\nSeção: S{n}\nURL: u{n}\nConteúdo:\n" + "x" * 300 for n in (1, 2, 3))
    assert texto == esperado
    assert [f["numero"] for f in fontes] == [1, 2, 3]


def test_documento_unico_e_cortado_no_limite():
    texto, fontes = preparar_contexto([_doc(1, 5000)], limite=1000)
    assert len(texto) == 1000 - 2 and len(fontes) == 1


def test_documento_sem_texto_e_ignorado_e_limite_baixo_levanta():
    texto, fontes = preparar_contexto([_doc(1, texto="   "), _doc(2, 300)], limite=2000)
    assert [f["id_documento"] for f in fontes] == ["d2"] and texto.startswith("[Fonte 1]")
    with pytest.raises(ValueError):
        preparar_contexto([_doc(1)], limite=499)


def test_fatia_abaixo_do_minimo_pula_o_documento(monkeypatch):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    docs = [_doc(1, pontuacao=1000.0), _doc(2, pontuacao=0.001)]
    texto, fontes = preparar_contexto(docs, limite=800)
    assert [f["id_documento"] for f in fontes] == ["d1"] and len(texto) <= 800


def _pontuacoes_e_total(docs, limite=6000):
    texto, fontes = preparar_contexto(docs, limite=limite)
    return texto, fontes


@pytest.mark.parametrize("pontuacoes", [
    [1.0, 0, 0],
    [0, 0, 0],
    [0.0, -1.0, 2.0],
    [float("nan"), True, None],
    [None, None, None],
    [-5, -1, 0],
])
def test_orcamento_nao_levanta_com_pontuacoes_zero_negativas_ou_invalidas(monkeypatch, pontuacoes):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    docs = [_doc(n, 500 if n == 1 else 5000) for n in (1, 2, 3)]
    for doc, nota in zip(docs, pontuacoes):
        if nota is not None:
            doc["pontuacao"] = nota
    texto, fontes = preparar_contexto(docs, limite=6000)
    assert fontes and fontes[0]["id_documento"] == "d1" and len(texto) <= 6000


def test_orcamento_reproducao_do_zero_division(monkeypatch):
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    docs = [_doc(1, 500, pontuacao=1.0), _doc(2, 5000, pontuacao=0), _doc(3, 5000, pontuacao=0)]
    texto, fontes = preparar_contexto(docs, limite=6000)
    assert len(fontes) == 3 and len(texto) <= 6000


@pytest.mark.parametrize("limite", [500, 501, 600, 6000])
def test_orcamento_propriedades_aleatorias(monkeypatch, limite):
    import random
    monkeypatch.setenv("RAG_ORCAMENTO", "1")
    sorteio = random.Random(1234)
    notas = [None, 0, 0.0, -1, 0.3, 1.0, 5.0, float("nan"), True]
    for _ in range(100):
        docs = []
        for n in range(1, sorteio.randint(1, 6) + 1):
            tamanho = sorteio.choice([0, 3, 50, 300, 5000])
            doc = _doc(n, tamanho)
            nota = sorteio.choice(notas)
            if nota is not None:
                doc["pontuacao"] = nota
            docs.append(doc)
        com_texto = [d["metadados"]["document_id"] for d in docs if d["texto"].strip()]
        texto, fontes = preparar_contexto(docs, limite=limite)
        assert len(texto) <= limite
        assert all(f["id_documento"] in com_texto for f in fontes)
        if com_texto:
            assert fontes and fontes[0]["id_documento"] == com_texto[0]


# ---- MMR na busca ----------------------------------------------------------------------------

_TEXTOS = {"d1": "alpha alpha", "d2": "alpha alpha beta", "d3": "alpha gamma", "d4": "delta"}
_VETORES = {"d1": [1.0, 0.0], "d2": [0.99, 0.14], "d3": [0.0, 1.0], "d4": [-1.0, 0.0]}


class _Espia(avaliacao_rag.ColecaoMemoria):
    def __init__(self, *args):
        super().__init__(*args)
        self.includes = []

    def query(self, query_embeddings, n_results, include):
        self.includes.append(list(include))
        return super().query(query_embeddings, n_results, include)


@pytest.fixture
def colecao(monkeypatch):
    chunks = [{"id": f"{d}#0", "texto": t,
               "metadados": {"document_id": d, "secao": "S" + d, "fonte": "f", "titulo": "t" + d, "indice": 0,
                             "versao_documentacao": "v", "coletado_em": "c"}} for d, t in _TEXTOS.items()]
    c = _Espia(chunks, [_VETORES[x["metadados"]["document_id"]] for x in chunks])
    monkeypatch.setattr(busca_chromadb, "obter_colecao", lambda: c)
    monkeypatch.setattr(busca_chromadb, "criar_embeddings", lambda: lambda ts: [[1.0, 0.0] for _ in ts])
    for nome in ("RAG_MMR", "RAG_MMR_LAMBDA", "RAG_MMR_CANDIDATOS"):
        monkeypatch.delenv(nome, raising=False)
    busca_chromadb.invalidar_indice_lexical()
    return c


def _buscar():
    return busca_chromadb.buscar_documentos("alpha", quantidade=4, modo="hibrido")


def test_mmr_desligado_nao_pede_embeddings(colecao):
    _buscar()
    assert colecao.includes and all("embeddings" not in inc for inc in colecao.includes)


def test_mmr_ligado_reordena_sem_mudar_pontuacao_nem_formato(colecao, monkeypatch):
    base = _buscar()
    monkeypatch.setenv("RAG_MMR", "1")
    monkeypatch.setenv("RAG_MMR_LAMBDA", "0.3")
    ligado = _buscar()
    assert any("embeddings" in inc for inc in colecao.includes)
    ids_base = [d["id"] for d in base]
    ids_mmr = [d["id"] for d in ligado]
    assert sorted(ids_base) == sorted(ids_mmr)
    assert ids_base[:2] == ["d1#0", "d2#0"]  # quase duplicados lado a lado sem MMR
    assert ids_mmr[0] == "d1#0" and ids_mmr[1] != "d2#0"  # MMR afasta o quase duplicado
    assert {d["id"]: d["pontuacao"] for d in base} == {d["id"]: d["pontuacao"] for d in ligado}
    assert all(set(d) == set(base[0]) for d in ligado)


def test_mmr_com_erro_cai_no_original_com_um_aviso(colecao, monkeypatch, caplog):
    sem = _buscar()
    monkeypatch.setenv("RAG_MMR", "1")

    def quebrar(*a, **k):
        raise RuntimeError("falha")
    monkeypatch.setattr(busca_chromadb.diversidade, "mmr", quebrar)
    with caplog.at_level("WARNING"):
        assert _buscar() == sem
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_mmr_sem_embeddings_na_resposta_cai_no_original(colecao, monkeypatch, caplog):
    sem = _buscar()
    monkeypatch.setenv("RAG_MMR", "1")
    original = colecao.query

    def sem_vetores(query_embeddings, n_results, include):
        r = original(query_embeddings, n_results, include)
        r.pop("embeddings", None)
        return r
    monkeypatch.setattr(colecao, "query", sem_vetores)
    with caplog.at_level("WARNING"):
        assert _buscar() == sem
    avisos = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(avisos) == 1 and avisos[0].name == busca_chromadb.log.name and "MMR" in avisos[0].getMessage()


def test_parametros_invalidos_usam_padroes(colecao, monkeypatch):
    monkeypatch.setenv("RAG_MMR", "1")
    monkeypatch.setenv("RAG_MMR_LAMBDA", "abc")
    monkeypatch.setenv("RAG_MMR_CANDIDATOS", "-5")
    assert len(_buscar()) == 4
