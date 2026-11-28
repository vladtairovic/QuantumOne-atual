"""Modos de avaliação da camada de consulta: ``hibrido+consulta_local`` e ``hibrido+consulta``."""
import json
import os
import sys

import numpy as np
import pytest

from rag import avaliacao_rag, busca_chromadb, consulta, traco

GOLDEN = {
    "como criar circuito de bell": ["como criar circuito de bell", "criar circuito bell circuit quantumcircuit"],
    "como medir o estado de um qubit e emaranhamento": [
        "como medir o estado de um qubit e emaranhamento",
        "medir estado qubit emaranhamento measure state statevector qubits entanglement entangled bell ghz"],
    "porta hadamard e CNOT no circuito": [
        "porta hadamard e CNOT no circuito", "porta hadamard cnot circuito gate circuit quantumcircuit"],
    "What is a quantum circuit and how do I measure it?": ["What is a quantum circuit and how do I measure it?"],
}

CASOS = [
    {"id": "c1", "pergunta": "como criar circuito de bell", "tema": "t", "relevantes": ["d1"], "split": "dev"},
    {"id": "c2", "pergunta": "como medir o estado de um qubit e emaranhamento", "tema": "t", "relevantes": ["d2"],
     "split": "dev"},
    {"id": "c3", "pergunta": "receita de bolo", "tema": "fora", "split": "dev"},
]


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    consulta.limpar_cache()
    yield
    consulta.limpar_cache()


# --- variantes_locais -------------------------------------------------------------------------

@pytest.mark.parametrize("pergunta", list(GOLDEN))
def test_variantes_locais_iguais_ao_fallback_antigo(pergunta):
    assert consulta.variantes_locais(pergunta) == GOLDEN[pergunta]


@pytest.mark.parametrize("pergunta", list(GOLDEN))
def test_expandir_sem_chave_devolve_variantes_locais(pergunta):
    assert consulta.expandir_consulta(pergunta) == GOLDEN[pergunta]


# --- corpus em memória ------------------------------------------------------------------------

def _vetor(texto):
    v = np.zeros(8, dtype=np.float32)
    for palavra in texto.lower().split():
        v[sum(map(ord, palavra)) % 8] += 1.0
    return v.tolist()


@pytest.fixture
def colecao(monkeypatch):
    textos = [("d1", "circuito bell quantumcircuit"), ("d2", "medir estado qubit measure"),
              ("d3", "outro assunto qualquer")]
    chunks = [{"id": f"x{n}", "texto": t, "metadados": {"document_id": d, "secao": "S", "fonte": "f", "titulo": "t" + d, "indice": 0,
                             "versao_documentacao": "v", "coletado_em": "c"}}
              for n, (d, t) in enumerate(textos)]
    col = avaliacao_rag.ColecaoMemoria(chunks, [_vetor(c["texto"]) for c in chunks])
    monkeypatch.setattr(busca_chromadb, "obter_colecao", lambda: col)
    monkeypatch.setattr(busca_chromadb, "criar_embeddings", lambda: (lambda textos: [_vetor(t) for t in textos]))
    busca_chromadb.invalidar_indice_lexical()
    yield col
    busca_chromadb.invalidar_indice_lexical()


def _espiao(monkeypatch):
    chamadas = []
    original = busca_chromadb.buscar_documentos

    def espiao(pergunta, *a, **kw):
        ambiente = {f"RAG_{c}": os.environ.get(f"RAG_{c}") for c in avaliacao_rag.CAMADAS_MEDIDAS}
        chamadas.append({"pergunta": pergunta, "consultas": kw.get("consultas"), "modo": kw.get("modo"),
                         "env": ambiente})
        return original(pergunta, *a, **kw)
    monkeypatch.setattr(busca_chromadb, "buscar_documentos", espiao)
    return chamadas


def test_modo_consulta_local_passa_variantes_locais(colecao, monkeypatch):
    chamadas = _espiao(monkeypatch)
    linhas = avaliacao_rag.avaliar_modo("hibrido+consulta_local", CASOS, 2)
    assert len(linhas) == 3
    assert [c["consultas"] for c in chamadas] == [consulta.variantes_locais(c["pergunta"]) for c in CASOS]
    assert all(c["modo"] == "hibrido" for c in chamadas)


def test_modo_consulta_usa_variantes_gravadas_e_cai_na_pergunta(colecao, monkeypatch):
    chamadas = _espiao(monkeypatch)
    variantes = {"c1": ["como criar circuito de bell", "bell state circuit"]}
    avaliacao_rag.avaliar_modo("hibrido+consulta", CASOS, 2, variantes=variantes)
    assert chamadas[0]["consultas"] == ["como criar circuito de bell", "bell state circuit"]
    assert chamadas[1]["consultas"] == [CASOS[1]["pergunta"]]
    assert chamadas[2]["consultas"] == [CASOS[2]["pergunta"]]


def test_modos_antigos_nao_passam_consultas(colecao, monkeypatch):
    chamadas = _espiao(monkeypatch)
    avaliacao_rag.avaliar_modo("hibrido", CASOS, 2)
    assert all(c["consultas"] is None for c in chamadas)


@pytest.mark.parametrize("modo", ["hibrido+consulta_local", "hibrido+consulta"])
def test_flags_fixadas_e_restauradas(colecao, monkeypatch, modo):
    for camada in avaliacao_rag.CAMADAS_MEDIDAS:
        monkeypatch.setenv(f"RAG_{camada}", "1")
    chamadas = _espiao(monkeypatch)
    avaliacao_rag.avaliar_modo(modo, CASOS, 2, variantes={})
    assert all(set(c["env"].values()) == {"0"} for c in chamadas)
    assert all(os.environ[f"RAG_{c}"] == "1" for c in avaliacao_rag.CAMADAS_MEDIDAS)


def test_resumo_dos_novos_modos(colecao):
    for modo in ("hibrido+consulta_local", "hibrido+consulta"):
        resumo = avaliacao_rag.resumir(avaliacao_rag.avaliar_modo(modo, CASOS, 2), 2)
        assert "recall@2" in resumo and "mrr" in resumo


# --- --variantes ------------------------------------------------------------------------------

def _escrever(tmp_path, dados, nome="v.json"):
    caminho = tmp_path / nome
    caminho.write_text(dados if isinstance(dados, str) else json.dumps(dados), encoding="utf-8")
    return caminho


@pytest.mark.parametrize("dados", [[], {"c1": []}, {"c1": ["ok", ""]}, {"c1": "texto"}, {"c1": [1]}, {"c1": ["  "]}])
def test_carregar_variantes_formato_invalido(tmp_path, dados):
    with pytest.raises(ValueError, match="--variantes"):
        avaliacao_rag.carregar_variantes(_escrever(tmp_path, dados), {"c1"})


def test_carregar_variantes_ids_desconhecidos_e_ausentes(tmp_path):
    with pytest.raises(ValueError, match="zzz"):
        avaliacao_rag.carregar_variantes(_escrever(tmp_path, {"zzz": ["a"], "c1": ["a"]}), {"c1"})
    lidas = avaliacao_rag.carregar_variantes(_escrever(tmp_path, {"c1": ["a", "b"]}), {"c1", "c2"})
    assert lidas == {"c1": ["a", "b"]}


def _proibir_pesado(monkeypatch):
    def pesado(*a, **k):
        raise AssertionError("carregou corpus/modelo")
    monkeypatch.setattr(avaliacao_rag, "carregar_corpus", pesado)
    monkeypatch.setattr(avaliacao_rag, "preparar", pesado)


def _main(monkeypatch, tmp_path, *args):
    gab = tmp_path / "gab.json"
    gab.write_text(json.dumps(CASOS), encoding="utf-8")
    monkeypatch.setattr(avaliacao_rag, "GABARITO", gab)
    monkeypatch.setattr(sys, "argv", ["avaliacao_rag", *args])
    avaliacao_rag.main()


@pytest.mark.parametrize("conteudo", ['{"c1": []}', '{"zzz": ["a"]}', "nao e json"])
def test_main_variantes_invalidas_saem_com_2_sem_carregar_modelo(monkeypatch, tmp_path, capsys, conteudo):
    _proibir_pesado(monkeypatch)
    arq = _escrever(tmp_path, conteudo)
    with pytest.raises(SystemExit) as saida:
        _main(monkeypatch, tmp_path, "--saida", str(tmp_path / "o.json"), "--variantes", str(arq))
    assert saida.value.code == 2
    assert "--variantes" in capsys.readouterr().err


def test_main_variantes_ids_desconhecidos_listados(monkeypatch, tmp_path, capsys):
    _proibir_pesado(monkeypatch)
    arq = _escrever(tmp_path, {"zzz": ["a"]})
    with pytest.raises(SystemExit):
        _main(monkeypatch, tmp_path, "--saida", str(tmp_path / "o.json"), "--variantes", str(arq))
    assert "zzz" in capsys.readouterr().err


def test_main_variantes_arquivo_ausente(monkeypatch, tmp_path, capsys):
    _proibir_pesado(monkeypatch)
    with pytest.raises(SystemExit) as saida:
        _main(monkeypatch, tmp_path, "--saida", str(tmp_path / "o.json"),
              "--variantes", str(tmp_path / "nao-existe.json"))
    assert saida.value.code == 2
    assert "--variantes" in capsys.readouterr().err


# --- --gerar-variantes --------------------------------------------------------------------------

def test_gerar_sem_chave_falha_antes_do_trabalho_pesado(monkeypatch, tmp_path, capsys):
    _proibir_pesado(monkeypatch)
    with pytest.raises(SystemExit) as saida:
        _main(monkeypatch, tmp_path, "--gerar-variantes", str(tmp_path / "v.json"))
    assert saida.value.code == 2
    assert "NVIDIA_API_KEY não configurada" in capsys.readouterr().err
    assert not (tmp_path / "v.json").exists()


def _falso_expandir(chamadas, falhas=()):
    """Fake de ``expandir_consulta``: ``falhas`` lista perguntas cuja chamada registra falha da LLM."""
    def expandir(pergunta, conversa=None, transporte=None):
        chamadas.append(pergunta)
        if pergunta in falhas:
            traco.registrar_fallback("consulta: reescrita pela LLM indisponível (ReadTimeout)")
            return consulta.variantes_locais(pergunta)
        if pergunta == CASOS[0]["pergunta"]:
            return [pergunta, "bell state quantum circuit"]
        return consulta.variantes_locais(pergunta)  # sucesso que coincide com o local: ainda é LLM
    return expandir


@pytest.fixture
def sem_pausa(monkeypatch):
    pausas = []
    monkeypatch.setattr(avaliacao_rag.time, "sleep", lambda s: pausas.append(s))
    return pausas


def _com_chave(monkeypatch, expandir):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setattr(consulta, "expandir_consulta", expandir)


def test_gerar_escreve_arquivo_retoma_e_conta(monkeypatch, tmp_path, capsys, sem_pausa):
    _proibir_pesado(monkeypatch)
    chamadas = []
    _com_chave(monkeypatch, _falso_expandir(chamadas))
    alvo = tmp_path / "v.json"
    _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    dados = json.loads(alvo.read_text(encoding="utf-8"))
    assert dados["c1"] == ["como criar circuito de bell", "bell state quantum circuit"]
    assert set(dados) == {"c1", "c2", "c3"}
    saida = capsys.readouterr().out
    assert "3 com variantes da LLM, 0 pendentes por falha, 0 já presentes" in saida
    assert "chave-de-teste" not in saida
    assert not list(tmp_path.glob("*.tmp"))
    chamadas.clear()  # segunda execução: retoma, pulando ids já gravados
    mtime = alvo.stat().st_mtime_ns
    _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    assert chamadas == []
    assert alvo.stat().st_mtime_ns == mtime  # nada novo: não reescreve
    assert "0 com variantes da LLM, 0 pendentes por falha, 3 já presentes" in capsys.readouterr().out


def test_gerar_retoma_so_o_que_falta(monkeypatch, tmp_path, sem_pausa):
    chamadas = []
    _com_chave(monkeypatch, _falso_expandir(chamadas))
    alvo = tmp_path / "v.json"
    alvo.write_text(json.dumps({"c1": ["como criar circuito de bell", "ja gravada"]}), encoding="utf-8")
    _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    assert CASOS[0]["pergunta"] not in chamadas and len(chamadas) == 2
    assert json.loads(alvo.read_text(encoding="utf-8"))["c1"][1] == "ja gravada"


def test_gerar_salva_a_cada_20_sem_limpar_cache(monkeypatch, tmp_path, sem_pausa):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    casos = [{"id": f"k{n}", "pergunta": f"pergunta {n}"} for n in range(45)]
    salvos = []
    original = avaliacao_rag._gravar_atomico

    def gravar(caminho, dados):
        salvos.append(len(dados))
        original(caminho, dados)
    monkeypatch.setattr(avaliacao_rag, "_gravar_atomico", gravar)
    limpezas = []
    monkeypatch.setattr(consulta, "limpar_cache", lambda: limpezas.append(1))
    monkeypatch.setattr(consulta, "expandir_consulta", lambda p, **k: [p, "var " + p])
    contagem = avaliacao_rag.gerar_variantes(casos, tmp_path / "v.json")
    assert salvos == [20, 40, 45]
    assert limpezas == []
    assert contagem == {"llm": 45, "pendentes": 0, "puladas": 0, "interrompida": False}


def test_falha_nao_e_gravada_e_e_repetida_na_proxima_execucao(monkeypatch, tmp_path, capsys, sem_pausa):
    chamadas = []
    falha = CASOS[1]["pergunta"]
    _com_chave(monkeypatch, _falso_expandir(chamadas, falhas={falha}))
    alvo = tmp_path / "v.json"
    with pytest.raises(SystemExit) as saida:
        _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    assert saida.value.code == 1
    assert set(json.loads(alvo.read_text(encoding="utf-8"))) == {"c1", "c3"}
    assert chamadas.count(falha) == 3 and sem_pausa == [1.0, 1.0]
    assert "2 com variantes da LLM, 1 pendentes por falha" in capsys.readouterr().out
    chamadas.clear()
    _com_chave(monkeypatch, _falso_expandir(chamadas))  # API de volta
    _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    assert chamadas == [falha]
    assert set(json.loads(alvo.read_text(encoding="utf-8"))) == {"c1", "c2", "c3"}


def test_retentativa_bem_sucedida_na_segunda_vez_e_gravada(monkeypatch, tmp_path, sem_pausa):
    tentativas = []

    def expandir(pergunta, conversa=None, transporte=None):
        tentativas.append(pergunta)
        if len(tentativas) == 1:
            traco.registrar_fallback("consulta: reescrita pela LLM indisponível (HTTPStatusError)")
            return [pergunta]
        return [pergunta, "variante"]
    _com_chave(monkeypatch, expandir)
    contagem = avaliacao_rag.gerar_variantes(CASOS[:1], tmp_path / "v.json")
    assert contagem["llm"] == 1 and contagem["pendentes"] == 0
    gravado = json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))
    assert gravado == {"c1": ["como criar circuito de bell", "variante"]}
    assert len(tentativas) == 2 and sem_pausa == [1.0]


def test_disjuntor_para_apos_5_falhas_seguidas_e_salva_progresso(monkeypatch, tmp_path, capsys, sem_pausa):
    casos = [{"id": f"k{n}", "pergunta": f"pergunta {n}"} for n in range(12)]
    chamadas = []
    _com_chave(monkeypatch, _falso_expandir(chamadas, falhas={c["pergunta"] for c in casos[2:]}))
    gab = tmp_path / "gab.json"
    gab.write_text(json.dumps(casos), encoding="utf-8")
    monkeypatch.setattr(avaliacao_rag, "GABARITO", gab)
    monkeypatch.setattr(sys, "argv", ["avaliacao_rag", "--gerar-variantes", str(tmp_path / "v.json")])
    with pytest.raises(SystemExit) as saida:
        avaliacao_rag.main()
    assert saida.value.code == 1
    assert len(chamadas) == 2 + 5 * 3  # 2 sucessos + 5 casos falhos com 3 tentativas; o resto nem é tentado
    assert set(json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))) == {"k0", "k1"}
    texto = capsys.readouterr().out
    assert "10 casos pendentes" in texto and "rode novamente para continuar" in texto


@pytest.mark.parametrize("conteudo", ['{"c1": ["a"', "[]", '{"c1": []}', '{"c1": [1]}', '"texto"'])
def test_arquivo_existente_invalido_sai_com_2_sem_chamar_llm(monkeypatch, tmp_path, capsys, conteudo):
    chamadas = []
    _com_chave(monkeypatch, _falso_expandir(chamadas))
    alvo = _escrever(tmp_path, conteudo, "v.json")
    with pytest.raises(SystemExit) as saida:
        _main(monkeypatch, tmp_path, "--gerar-variantes", str(alvo))
    assert saida.value.code == 2
    erro = capsys.readouterr().err
    assert "v.json" in erro and "apague" in erro
    assert chamadas == [] and alvo.read_text(encoding="utf-8") == conteudo


def test_ctrl_c_no_meio_preserva_o_que_ja_foi_gerado(monkeypatch, tmp_path, sem_pausa):
    n = []

    def expandir(pergunta, conversa=None, transporte=None):
        n.append(pergunta)
        if len(n) == 3:
            raise KeyboardInterrupt
        return [pergunta, "v"]
    _com_chave(monkeypatch, expandir)
    casos = [{"id": f"k{i}", "pergunta": f"pergunta {i}"} for i in range(5)]
    with pytest.raises(KeyboardInterrupt):
        avaliacao_rag.gerar_variantes(casos, tmp_path / "v.json")
    assert set(json.loads((tmp_path / "v.json").read_text(encoding="utf-8"))) == {"k0", "k1"}


def test_sem_nada_novo_nao_cria_arquivo(monkeypatch, tmp_path, sem_pausa):
    _com_chave(monkeypatch, lambda p, **k: pytest.fail("não deveria chamar"))
    assert avaliacao_rag.gerar_variantes([], tmp_path / "v.json")["llm"] == 0
    assert not (tmp_path / "v.json").exists()


# --- main: modos no relatório ---------------------------------------------------------------

class _ModeloFalso:
    @staticmethod
    def encode(textos, **kw):
        return np.array([_vetor(t) for t in textos])


def _main_avaliacao(monkeypatch, tmp_path, colecao, *args):
    monkeypatch.setattr(avaliacao_rag, "carregar_corpus", lambda pastas: [])
    monkeypatch.setattr(avaliacao_rag, "preparar", lambda docs: ([], None, 0.0, _ModeloFalso()))
    monkeypatch.setattr(avaliacao_rag, "ColecaoMemoria", lambda chunks, vetores: colecao)
    monkeypatch.setattr(avaliacao_rag.reranker, "disponivel", lambda: False)
    saida = tmp_path / "rel.json"
    _main(monkeypatch, tmp_path, "--k", "2", "--saida", str(saida), *args)
    return json.loads(saida.read_text(encoding="utf-8"))


def test_main_relatorio_tem_consulta_local_e_consulta_so_com_variantes(monkeypatch, tmp_path, colecao, capsys):
    relatorio = _main_avaliacao(monkeypatch, tmp_path, colecao)
    assert "hibrido+consulta_local" in relatorio["modos"] and "hibrido+consulta" not in relatorio["modos"]
    assert "hibrido+consulta_local" in capsys.readouterr().out
    arq = _escrever(tmp_path, {"c1": ["como criar circuito de bell", "bell"]}, "var.json")
    relatorio = _main_avaliacao(monkeypatch, tmp_path, colecao, "--variantes", str(arq))
    assert {"hibrido+consulta_local", "hibrido+consulta"} <= set(relatorio["modos"])
    assert "hibrido+consulta " in capsys.readouterr().out
