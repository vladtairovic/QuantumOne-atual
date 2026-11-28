import threading

import pytest

from rag import avaliacao_rag, busca_chromadb, reranker


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch):
    monkeypatch.delenv("RAG_RERANKER", raising=False)
    monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
    monkeypatch.delenv("RAG_RERANKER_PESO", raising=False)
    reranker._resetar()
    yield
    reranker._resetar()


def _cands():
    return [{"id": i, "texto": f"texto {i}", "nota": 1.0 - n / 10} for n, i in enumerate("ABCD")]


def _pontuador_b(pares):
    return [9.0 if t == "texto B" else 0.0 for _, t in pares]


def test_pontuador_fake_coloca_b_primeiro_e_preserva_ids():
    saida = reranker.reordenar("q", _cands(), pontuador=_pontuador_b)
    assert saida[0]["id"] == "B"
    assert sorted(c["id"] for c in saida) == list("ABCD")
    assert all("nota_reranker" in c for c in saida)
    assert saida[0]["nota_reranker"] == 9.0


def test_topo_limita_o_que_e_reordenado_e_mantem_o_resto_na_ordem():
    saida = reranker.reordenar("q", _cands(), topo=2, pontuador=lambda pares: [0.0, 5.0])
    assert [c["id"] for c in saida] == ["B", "A", "C", "D"]
    assert "nota_reranker" not in saida[2] and "nota_reranker" not in saida[3]


def test_lista_vazia():
    assert reranker.reordenar("q", [], pontuador=_pontuador_b) == []
    assert reranker.reordenar("q", []) == []


def test_modelo_ausente_devolve_entrada_intacta_e_avisa_uma_vez(monkeypatch, caplog):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "modelo/inexistente")

    def falha(nome):
        raise OSError("sem modelo")

    monkeypatch.setattr(reranker, "_criar_modelo", falha)
    entrada = _cands()
    with caplog.at_level("WARNING", logger=reranker.log.name):
        assert reranker.reordenar("q", entrada) == _cands()
        assert reranker.reordenar("q", entrada) == _cands()
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
    assert reranker.disponivel() is False


def test_sem_variavel_de_modelo_devolve_entrada_intacta(caplog):
    with caplog.at_level("WARNING", logger=reranker.log.name):
        assert reranker.reordenar("q", _cands()) == _cands()
    assert len(caplog.records) == 1


def test_carga_concorrente_chama_o_carregador_uma_vez(monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "modelo/falso")
    chamadas = []

    class Modelo:
        def predict(self, pares):
            return [1.0] * len(pares)

    def carregador(nome):
        chamadas.append(nome)
        threading.Event().wait(0.05)
        return Modelo()

    monkeypatch.setattr(reranker, "_criar_modelo", carregador)
    barreira = threading.Barrier(8)
    resultados = []

    def trabalho():
        barreira.wait()
        resultados.append(reranker._obter_modelo())

    fios = [threading.Thread(target=trabalho) for _ in range(8)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    assert len(chamadas) == 1
    assert len(resultados) == 8 and all(r is resultados[0] for r in resultados)


def test_modelo_carregado_pontua_pelos_pares(monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "modelo/falso")

    class Modelo:
        def predict(self, pares):
            return [9.0 if t == "texto C" else 0.0 for _, t in pares]

    monkeypatch.setattr(reranker, "_criar_modelo", lambda nome: Modelo())
    assert reranker.reordenar("q", _cands())[0]["id"] == "C"
    assert reranker.disponivel() is True


_TEXTOS = {"d1": "alpha alpha alpha alpha alpha alpha", "d2": "alpha beta alpha beta alpha beta",
           "d3": "gamma gamma gamma gamma", "d4": "delta delta delta delta"}


# ---- integração em buscar_documentos -------------------------------------------------

def _colecao():
    textos = _TEXTOS
    chunks = [{"id": f"{d}#0", "texto": t,
               "metadados": {"document_id": d, "secao": "S", "fonte": "f", "titulo": "t" + d, "indice": 0,
                             "versao_documentacao": "v", "coletado_em": "c"}}
              for d, t in textos.items()]
    vetores = [[1.0, 0.0] if "alpha" in c["texto"] else [0.0, 1.0] for c in chunks]
    return avaliacao_rag.ColecaoMemoria(chunks, vetores)


@pytest.fixture
def busca(monkeypatch):
    colecao = _colecao()
    monkeypatch.setattr(busca_chromadb, "obter_colecao", lambda: colecao)
    monkeypatch.setattr(busca_chromadb, "criar_embeddings", lambda: lambda ts: [[1.0, 0.0] for _ in ts])
    busca_chromadb.invalidar_indice_lexical()
    return lambda: busca_chromadb.buscar_documentos("alpha", quantidade=3, modo="hibrido")


def test_desligado_nao_chama_o_reranker_e_mantem_ordem_e_notas(monkeypatch, busca):
    def proibido(*a, **k):
        raise AssertionError("reranker chamado com a flag desligada")

    monkeypatch.setattr(reranker, "reordenar", proibido)
    sem_flag = busca()
    monkeypatch.setenv("RAG_RERANKER", "0")
    com_zero = busca()
    assert [d["id"] for d in sem_flag] == ["d1#0", "d2#0", "d3#0"]
    assert [(d["id"], d["pontuacao"]) for d in sem_flag] == [(d["id"], d["pontuacao"]) for d in com_zero]


def test_ligado_usa_o_pontuador_e_mistura_com_a_nota_rrf(monkeypatch, busca):
    base = busca()
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_PESO", "1")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", lambda pares: [9.0 if "beta" in t else 0.0 for _, t in pares])
    ligado = busca()
    assert [d["id"] for d in base][:2] == ["d1#0", "d2#0"]
    assert ligado[0]["id"] == "d2#0"
    assert {d["id"] for d in ligado} <= {"d1#0", "d2#0", "d3#0", "d4#0"}
    assert ligado[0]["pontuacao"] > ligado[1]["pontuacao"]


def test_ligado_com_peso_zero_preserva_a_ordem_rrf(monkeypatch, busca):
    base = busca()
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_PESO", "0")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", lambda pares: [9.0 if "beta" in t else 0.0 for _, t in pares])
    assert [d["id"] for d in busca()] == [d["id"] for d in base]


def test_ligado_sem_modelo_nao_quebra_e_mantem_a_ordem(monkeypatch, busca):
    base = busca()
    monkeypatch.setenv("RAG_RERANKER", "1")
    assert [(d["id"], d["pontuacao"]) for d in busca()] == [(d["id"], d["pontuacao"]) for d in base]


# ---- modo hibrido+reranker na avaliação ---------------------------------------------

def test_ambiente_temporario_restaura_o_valor_anterior(monkeypatch):
    import os

    monkeypatch.delenv("RAG_RERANKER", raising=False)
    with avaliacao_rag.ambiente(RAG_RERANKER="1"):
        assert os.environ["RAG_RERANKER"] == "1"
    assert "RAG_RERANKER" not in os.environ
    monkeypatch.setenv("RAG_RERANKER", "0")
    with pytest.raises(RuntimeError):
        with avaliacao_rag.ambiente(RAG_RERANKER="1"):
            raise RuntimeError("x")
    assert os.environ["RAG_RERANKER"] == "0"


def test_avaliar_modo_hibrido_reranker_liga_flag_so_nesse_modo(monkeypatch):
    import os

    vistos = []

    def falsa(pergunta, quantidade=3, *, tempos=None, modo=None, biblioteca=None, reordenar=True):
        vistos.append((modo, reordenar, os.environ.get("RAG_RERANKER")))
        return []

    monkeypatch.setattr(busca_chromadb, "buscar_documentos", falsa)
    monkeypatch.setattr(busca_chromadb, "avaliar_evidencia", lambda p, s: {"suficiente": False, "cobertura": 0.0})
    monkeypatch.delenv("RAG_RERANKER", raising=False)
    caso = {"id": "c", "tema": "t", "pergunta": "q"}
    avaliacao_rag.avaliar_modo("hibrido+reranker", [caso], 5)
    avaliacao_rag.avaliar_modo("hibrido", [caso], 5)
    assert vistos == [("hibrido", True, "1"), ("hibrido", True, "0")]  # o modo sem reranker fixa a flag em "0"
    assert "RAG_RERANKER" not in os.environ


CAMADAS_MEDIDAS = ("RAG_RERANKER", "RAG_CONSULTA", "RAG_MMR", "RAG_ORCAMENTO", "RAG_VERIFICACAO")


@pytest.mark.parametrize("modo,ligada", [
    ("linha_de_base", None), ("vetorial", None), ("lexical", None), ("hibrido", None),
    ("hibrido+reranker", "RAG_RERANKER"), ("hibrido+mmr", "RAG_MMR")])
def test_cada_modo_fixa_todas_as_flags_mesmo_com_o_shell_ligado(monkeypatch, modo, ligada):
    import os

    vistos = []

    def falsa(pergunta, quantidade=3, *, tempos=None, modo=None, biblioteca=None, reordenar=True):
        vistos.append({n: os.environ.get(n) for n in CAMADAS_MEDIDAS})
        return []

    monkeypatch.setattr(busca_chromadb, "buscar_documentos", falsa)
    monkeypatch.setattr(busca_chromadb, "avaliar_evidencia", lambda p, s: {"suficiente": False, "cobertura": 0.0})
    for nome in CAMADAS_MEDIDAS:
        monkeypatch.setenv(nome, "1")  # como num shell com as camadas exportadas
    avaliacao_rag.avaliar_modo(modo, [{"id": "c", "tema": "t", "pergunta": "q"}], 5)
    esperado = {n: "1" if n == ligada else "0" for n in CAMADAS_MEDIDAS}
    assert vistos == [esperado]
    assert all(os.environ[n] == "1" for n in CAMADAS_MEDIDAS)  # restauradas depois do modo


def test_respostas_ilegivel_vira_erro_de_uso(monkeypatch, tmp_path, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["avaliacao_rag", "--saida", str(tmp_path / "s.json"),
                                      "--respostas", str(tmp_path / "nao_existe.json")])
    with pytest.raises(SystemExit) as saida:
        avaliacao_rag.main()
    assert saida.value.code == 2
    assert "--respostas" in capsys.readouterr().err
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    monkeypatch.setattr(sys, "argv", ["avaliacao_rag", "--saida", str(tmp_path / "s.json"), "--respostas", str(pasta)])
    with pytest.raises(SystemExit) as saida:
        avaliacao_rag.main()
    assert saida.value.code == 2


# ---- falhas do pontuador: sempre cai na lista de entrada ------------------------------

def _levanta(pares):
    raise RuntimeError("inferência quebrou")


@pytest.mark.parametrize("pontuador", [
    _levanta,
    lambda pares: [1.0, 2.0],  # poucas notas
    lambda pares: [1.0] * 9,  # notas demais
    lambda pares: [1.0, float("nan"), 2.0, 3.0],
    lambda pares: [1.0, float("inf"), 2.0, 3.0],
], ids=["excecao", "poucas", "demais", "nan", "inf"])
def test_pontuador_defeituoso_devolve_entrada_e_avisa_uma_vez(pontuador, caplog):
    entrada = _cands()
    with caplog.at_level("WARNING", logger=reranker.log.name):
        assert reranker.reordenar("q", entrada, pontuador=pontuador) == _cands()
        assert reranker.reordenar("q", entrada, pontuador=pontuador) == _cands()
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_busca_com_pontuador_defeituoso_e_identica_a_desligada(monkeypatch, busca):
    base = busca()
    monkeypatch.setenv("RAG_RERANKER", "1")
    for pontuador in (_levanta, lambda pares: [1.0], lambda pares: [float("nan")] * len(pares)):
        monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", pontuador)
        assert busca() == base


# ---- RAG_RERANKER_PESO ------------------------------------------------------------------

@pytest.mark.parametrize("valor,esperado", [("abc", 0.7), ("", 0.7), ("nan", 0.7), ("-3", 0.0), ("2.5", 1.0),
                                            ("0.25", 0.25)])
def test_peso_do_reranker_e_lido_com_defesa(monkeypatch, valor, esperado):
    monkeypatch.setenv("RAG_RERANKER_PESO", valor)
    assert busca_chromadb._peso_reranker() == esperado


def test_busca_com_peso_invalido_nao_levanta(monkeypatch, busca):
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_PESO", "lixo")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", lambda pares: [float(len(t)) for _, t in pares])
    assert busca()


# ---- escala da pontuação ------------------------------------------------------------------

def test_pontuacao_ligada_fica_na_escala_rrf(monkeypatch, busca):
    base = {d["id"]: d["pontuacao"] for d in busca()}
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_PESO", "1")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", lambda pares: [9.0 if "beta" in t else 0.0 for _, t in pares])
    ligado = busca()
    teto = max(base.values()) * 1.2 * 1.25 * 1.15
    assert all(d["pontuacao"] <= teto for d in ligado)
    assert max(d["pontuacao"] for d in ligado) <= max(base.values()) * 1.0001


def test_ordem_neural_igual_a_rrf_preserva_ordem_e_notas(monkeypatch, busca):
    base = busca()
    ordem = {d["id"].split("#")[0]: n for n, d in enumerate(reversed(base))}
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_PESO", "0")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO",
                        lambda pares: [float(sum(n for d, n in ordem.items() if t == _TEXTOS[d])) for _, t in pares])
    ligado = busca()
    assert [d["id"] for d in ligado] == [d["id"] for d in base]
    for a, b in zip(ligado, base):
        assert a["pontuacao"] == pytest.approx(b["pontuacao"], abs=1e-5)


def test_resposta_local_mantem_mais_de_uma_secao_com_reranker(monkeypatch, busca):
    from rag import resposta_local

    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO",
                        lambda pares: [{_TEXTOS["d1"]: 0.9, _TEXTOS["d2"]: 1.0}.get(t, 0.0) for _, t in pares])
    secoes = busca()
    resposta = resposta_local.gerar("alpha", secoes)
    assert resposta is not None and len(resposta["fontes_usadas"]) > 1


# ---- disponivel(): cache local do HF, sem rede -----------------------------------------

def _cache_hf(raiz, arquivos, commit="abc123"):
    repo = raiz / "models--org--nome"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(commit)
    snap = repo / "snapshots" / commit
    snap.mkdir(parents=True)
    for a in arquivos:
        (snap / a).write_text("x")
    return raiz


def test_disponivel_com_cache_enxuto(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "org/nome")
    assert reranker.disponivel(_cache_dir=_cache_hf(tmp_path, ["config.json", "model.safetensors"])) is True


def test_indisponivel_com_cache_vazio(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "org/nome")
    assert reranker.disponivel(_cache_dir=tmp_path) is False


def test_indisponivel_sem_config_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", "org/nome")
    assert reranker.disponivel(_cache_dir=_cache_hf(tmp_path, ["model.safetensors"])) is False


def test_indisponivel_sem_variavel(tmp_path):
    assert reranker.disponivel(_cache_dir=_cache_hf(tmp_path, ["config.json"])) is False


def test_disponivel_com_pasta_local(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_RERANKER_MODEL", str(tmp_path))
    assert reranker.disponivel() is True


# ---- RAG_RERANKER_TOPO -----------------------------------------------------------------

@pytest.mark.parametrize("valor,esperado", [(None, 30), ("20", 20), (" 15 ", 15), ("abc", 30), ("", 30),
                                            ("nan", 30), ("3", 5), ("500", 100), ("12.5", 30)])
def test_topo_do_reranker_e_lido_com_defesa(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv("RAG_RERANKER_TOPO", raising=False)
    else:
        monkeypatch.setenv("RAG_RERANKER_TOPO", valor)
    assert busca_chromadb._topo_reranker() == esperado


@pytest.mark.parametrize("valor,esperado", [(None, 30), ("7", 7)])
def test_busca_repassa_o_topo_ao_reranker(monkeypatch, busca, valor, esperado):
    monkeypatch.setenv("RAG_RERANKER", "1")
    if valor:
        monkeypatch.setenv("RAG_RERANKER_TOPO", valor)
    monkeypatch.setattr(reranker, "_PONTUADOR_PADRAO", lambda pares: [0.0] * len(pares))
    recebidos = []
    original = reranker.reordenar
    monkeypatch.setattr(reranker, "reordenar", lambda *a, **k: (recebidos.append(k.get("topo")), original(*a, **k))[1])
    busca()
    assert recebidos == [esperado]


# --- pré-carga/aquecimento na partida ---
class _ModeloFalso:
    def __init__(self):
        self.chamadas = []

    def predict(self, pares):
        self.chamadas.append(list(pares))
        return [0.5 for _ in pares]


def _ligar(monkeypatch, modelo="org/modelo-falso"):
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setenv("RAG_RERANKER_MODEL", modelo)


def test_pre_carregar_desligado_nao_carrega(monkeypatch):
    carregados = []
    monkeypatch.setattr(reranker, "_criar_modelo", lambda nome: carregados.append(nome) or _ModeloFalso())
    monkeypatch.setenv("RAG_RERANKER_MODEL", "org/modelo-falso")
    assert reranker.pre_carregar() is False
    assert carregados == []


def test_pre_carregar_ligado_sem_modelo_nao_carrega(monkeypatch):
    monkeypatch.setenv("RAG_RERANKER", "1")
    monkeypatch.setattr(reranker, "_criar_modelo", lambda nome: pytest.fail("não deveria carregar"))
    assert reranker.pre_carregar() is False


def test_pre_carregar_ligado_carrega_uma_vez_e_aquece(monkeypatch):
    _ligar(monkeypatch)
    modelo, carregados = _ModeloFalso(), []
    monkeypatch.setattr(reranker, "_criar_modelo", lambda nome: carregados.append(nome) or modelo)
    assert reranker.pre_carregar() is True
    assert reranker.pre_carregar() is True  # idempotente
    assert carregados == ["org/modelo-falso"]
    assert len(modelo.chamadas) == 1 and len(modelo.chamadas[0]) == 1
    # a busca seguinte reaproveita o mesmo modelo
    reranker.reordenar("q", _cands())
    assert carregados == ["org/modelo-falso"]


def test_pre_carregar_falha_devolve_false_sem_excecao_e_nao_repete(monkeypatch, caplog):
    _ligar(monkeypatch)
    tentativas = []

    def quebra(nome):
        tentativas.append(nome)
        raise OSError("sem modelo no disco")

    monkeypatch.setattr(reranker, "_criar_modelo", quebra)
    with caplog.at_level("WARNING"):
        assert reranker.pre_carregar() is False
        assert reranker.pre_carregar() is False
    assert len(tentativas) == 1
    assert any("indisponível" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert reranker.reordenar("q", _cands()) == _cands()  # consultas seguem com a ordem original


def test_pre_carregar_predict_que_falha_nao_levanta(monkeypatch):
    _ligar(monkeypatch)

    class Quebrado:
        def predict(self, pares):
            raise RuntimeError("falha")

    monkeypatch.setattr(reranker, "_criar_modelo", lambda nome: Quebrado())
    assert reranker.pre_carregar() is False


def test_partida_da_api_chama_pre_carregar(monkeypatch):
    import servicos

    class _Modelo:
        def encode(self, *a, **k):
            return []

    ordem = []
    # O aquecimento dos embeddings mudou de forma entre versões (criar_embeddings x obter_modelo): cobre as duas.
    monkeypatch.setattr(servicos, "obter_modelo", lambda: ordem.append("embedding") or _Modelo(), raising=False)
    monkeypatch.setattr(servicos, "criar_embeddings",
                        lambda: (lambda textos: ordem.append("embedding")), raising=False)
    monkeypatch.setattr(servicos.reranker, "pre_carregar", lambda: ordem.append("reranker") or True)

    class _Cliente:
        def heartbeat(self):
            pass

    monkeypatch.setattr(servicos, "obter_cliente", lambda: _Cliente())
    monkeypatch.setattr(servicos, "obter_colecao", lambda: object())
    monkeypatch.setattr(servicos.documentos, "carregar_uploads_iniciais", lambda c: None)
    from rag import conhecimento_interno
    monkeypatch.setattr(conhecimento_interno, "indexar", lambda c: {})
    servicos.ServicosIntegrados().inicializar()
    assert ordem == ["embedding", "reranker"]
