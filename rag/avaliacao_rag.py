"""Avaliação objetiva e reproduzível do RAG do QuantumOne.

Executa o MESMO código de recuperação de produção (``busca_chromadb``) sobre
um corpus local em memória, com o modelo de embeddings real, e mede:

- Recall@K, Precision@K, MRR e nDCG@K (relevância em nível de documento);
- cobertura por tema;
- detecção de perguntas sem resposta na base (fora do domínio);
- precisão das citações e literalidade da resposta extrativa local;
- latência de recuperação (média e p95).

Compara ``linha_de_base`` (busca vetorial pura, como o sistema anterior),
``vetorial`` (vetorial + reordenação nova), ``lexical`` (BM25 + reordenação)
e ``hibrido`` (RRF dos dois + reordenação, o padrão).

Uso:
    python -m rag.avaliacao_rag --snapshots artifacts/conhecimento \
        --saida docs/benchmarks/rag-avaliacao.json --split dev
"""
import argparse
import contextlib
import json
import math
import os
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import busca_chromadb, cliente_llm, conhecimento_interno, consulta, reranker, resposta_local, traco
from .preparar_chunks import gerar_chunks
from .suporte import pontuar_suporte
from .texto import frases

ROOT = Path(__file__).resolve().parents[1]
GABARITO = ROOT / "tests" / "rag_benchmark.json"


class ColecaoMemoria:
    """Subconjunto da API do ChromaDB usado pela busca, com distância l2."""

    def __init__(self, chunks, vetores):
        self.ids = [c["id"] for c in chunks]
        self.textos = {c["id"]: c["texto"] for c in chunks}
        self.metas = {c["id"]: c["metadados"] for c in chunks}
        self.matriz = np.asarray(vetores, dtype=np.float32)

    def count(self):
        return len(self.ids)

    def query(self, query_embeddings, n_results, include):
        q = np.asarray(query_embeddings[0], dtype=np.float32)
        dist = ((self.matriz - q) ** 2).sum(axis=1)
        ordem = np.argsort(dist)[:n_results]
        ids = [self.ids[i] for i in ordem]
        resultado = {"ids": [ids], "metadatas": [[self.metas[i] for i in ids]],
                     "documents": [[self.textos[i] for i in ids]], "distances": [[float(dist[i]) for i in ordem]]}
        if "embeddings" in include:
            resultado["embeddings"] = [[self.matriz[i].tolist() for i in ordem]]
        return resultado

    def get(self, where=None, include=None, limit=None, offset=0, ids=None):
        filtros = (where or {}).get("$and", [where] if where else [])
        selecionados = [i for i in (ids or self.ids)
                        if all(all(self.metas[i].get(k) == v for k, v in f.items()) for f in filtros)]
        selecionados = selecionados[offset:offset + limit if limit is not None else None]
        return {"ids": selecionados, "documents": [self.textos[i] for i in selecionados],
                "metadatas": [self.metas[i] for i in selecionados]}


def carregar_corpus(pastas_snapshots):
    documentos = []
    for pasta in pastas_snapshots:
        for arquivo in sorted(Path(pasta).glob("*.json")):
            if arquivo.name.endswith(".previous.json") or arquivo.name == "status.json":
                continue
            documento = json.loads(arquivo.read_text(encoding="utf-8"))
            if documento.get("secoes"):
                documentos.append(documento)
    for documento in conhecimento_interno.carregar():
        secoes = [{"titulo": s["titulo"], "blocos": [{"tipo": b["tipo"], "texto": b["texto"]} for b in s["blocos"]]}
                  for s in documento["secoes"]]
        documentos.append({**documento, "secoes": secoes})
    vistos, unicos = set(), []
    for documento in documentos:
        if documento["document_id"] not in vistos:
            vistos.add(documento["document_id"])
            unicos.append(documento)
    return unicos


def preparar(documentos):
    from .configuracao import MODELO_EMBEDDINGS, obter_modelo, limite_tokens, prefixo
    modelo = obter_modelo()
    chunks = []
    for documento in documentos:
        documento.setdefault("versao_documentacao", "snapshot")
        documento.setdefault("coletado_em", "snapshot")
        chunks.extend(gerar_chunks(documento, modelo.tokenizer, limite_tokens(), MODELO_EMBEDDINGS))
    inicio = time.perf_counter()
    marca = prefixo("passagem", MODELO_EMBEDDINGS)
    vetores = modelo.encode([marca + c["texto"] for c in chunks], normalize_embeddings=True, convert_to_numpy=True,
                            batch_size=64, show_progress_bar=False)
    return chunks, vetores, time.perf_counter() - inicio, modelo


def _metricas(recuperados, relevantes, k):
    top = recuperados[:k]
    acertos = [1 if d in relevantes else 0 for d in top]
    recall = len(set(top) & relevantes) / len(relevantes)
    precisao = sum(acertos) / k
    rr = next((1 / (i + 1) for i, a in enumerate(acertos) if a), 0.0)
    dcg = sum(a / math.log2(i + 2) for i, a in enumerate(acertos))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevantes))))
    return recall, precisao, rr, dcg / ideal if ideal else 0.0


@contextlib.contextmanager
def ambiente(**variaveis):
    """Define variáveis de ambiente só dentro do bloco e restaura o estado anterior."""
    antes = {nome: os.environ.get(nome) for nome in variaveis}
    os.environ.update(variaveis)
    try:
        yield
    finally:
        for nome, valor in antes.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = valor


CAMADAS_MEDIDAS = ("RERANKER", "CONSULTA", "MMR", "ORCAMENTO", "VERIFICACAO")


MODOS_CONSULTA = ("hibrido+consulta_local", "hibrido+consulta", "hibrido+reranker+consulta", "hibrido+completo")
# Camadas de recuperação que cada modo liga (as demais ficam em "0"). Os modos combinados medem as
# camadas juntas, como na implantação com tudo ligado; a reescrita entra pelas variantes gravadas.
CAMADAS_DO_MODO = {"hibrido+reranker": ("RERANKER",), "hibrido+mmr": ("MMR",),
                   "hibrido+reranker+mmr": ("RERANKER", "MMR"), "hibrido+reranker+consulta": ("RERANKER",),
                   "hibrido+completo": ("RERANKER", "MMR")}
MODOS_COM_RERANKER = tuple(m for m, camadas in CAMADAS_DO_MODO.items() if "RERANKER" in camadas)
TODOS_OS_MODOS = ("linha_de_base", "vetorial", "lexical", "hibrido", "hibrido+consulta_local", "hibrido+consulta",
                  *CAMADAS_DO_MODO)


def _modo_de_busca(modo):
    """Modo aceito por ``buscar_documentos`` e variáveis de ambiente do modo avaliado.

    Todo modo fixa TODAS as flags de camada (``RAG_*``) em "0", exceto as que ele mesmo liga:
    assim, flags exportadas no shell não vazam para modos que não as pedem."""
    # Os modos de consulta passam ``consultas=`` explicitamente; RAG_CONSULTA fica em "0" como nos demais.
    ligadas = CAMADAS_DO_MODO.get(modo, ())
    variaveis = {f"RAG_{camada}": "1" if camada in ligadas else "0" for camada in CAMADAS_MEDIDAS}
    if modo == "linha_de_base":
        return "vetorial", variaveis
    if ligadas or modo in MODOS_CONSULTA:
        return "hibrido", variaveis
    return modo, variaveis


def _consultas_do_caso(modo, caso, variantes):
    """Consultas passadas à busca: só os modos de consulta usam ``consultas=``."""
    if modo == "hibrido+consulta_local":
        return consulta.variantes_locais(caso["pergunta"])
    if modo == "hibrido+consulta":
        return (variantes or {}).get(caso["id"]) or [caso["pergunta"]]
    if modo in ("hibrido+reranker+consulta", "hibrido+completo"):
        if variantes is None:  # sem variantes gravadas da LLM, usa a variante determinística do glossário
            return consulta.variantes_locais(caso["pergunta"])
        return variantes.get(caso["id"]) or [caso["pergunta"]]
    return None


def avaliar_modo(modo, casos, k, variantes=None):
    linhas = []
    modo_busca, variaveis = _modo_de_busca(modo)
    for caso in casos:
        tempos = {}
        inicio = time.perf_counter()
        extras = {}
        consultas = _consultas_do_caso(modo, caso, variantes)
        if consultas is not None:  # só os modos de consulta passam o argumento
            extras["consultas"] = consultas
        with ambiente(**variaveis):
            secoes = busca_chromadb.buscar_documentos(
                caso["pergunta"], quantidade=k * 2, tempos=tempos, modo=modo_busca,
                reordenar=modo != "linha_de_base", **extras)
        latencia = time.perf_counter() - inicio
        docs = list(dict.fromkeys(s["metadados"]["document_id"] for s in secoes))
        evidencia = busca_chromadb.avaliar_evidencia(caso["pergunta"], secoes[:k])
        linha = {"id": caso["id"], "tema": caso["tema"], "latencia_s": latencia,
                 "evidencia_suficiente": evidencia["suficiente"], "cobertura": evidencia["cobertura"],
                 "recuperados": docs[:k]}
        if caso.get("relevantes"):
            relevantes = set(caso["relevantes"])
            r, p, rr, ndcg = _metricas(docs, relevantes, k)
            local = resposta_local.gerar(caso["pergunta"], secoes[:k])
            citacoes, corretas, literais, total_frases = 0, 0, 0, 0
            suportes = []
            if local:
                for linha_resp in local["response"].splitlines():
                    if not linha_resp.startswith("- ") or "[Fonte " not in linha_resp:
                        continue
                    numero = int(linha_resp.rsplit("[Fonte ", 1)[1].rstrip("]"))
                    citacoes += 1
                    fonte = secoes[numero - 1]
                    corretas += fonte["metadados"]["document_id"] in relevantes
                    texto_frase = linha_resp[2:].rsplit(" [Fonte ", 1)[0]
                    total_frases += 1
                    literais += texto_frase in frases(fonte["texto"])
                    suportes.append(pontuar_suporte(texto_frase, [fonte["texto"]]))
            linha.update(recall=r, precisao=p, rr=rr, ndcg=ndcg, citacoes=citacoes,
                         citacoes_corretas=corretas, frases_literais=literais, frases=total_frases,
                         fidelidade_local=statistics.mean(suportes) if suportes else None,
                         primeiro_relevante=next((d for d in docs if d in relevantes), None))
        linhas.append(linha)
    return linhas


def _media_ou_none(valores):
    valores = list(valores)
    return round(statistics.mean(valores), 4) if valores else None


def resumir(linhas, k):
    respondiveis = [l for l in linhas if "recall" in l]
    fora = [l for l in linhas if "recall" not in l]
    latencias = sorted(l["latencia_s"] for l in linhas)
    temas = {}
    for l in respondiveis:
        temas.setdefault(l["tema"], []).append(l["recall"])
    citacoes = sum(l["citacoes"] for l in respondiveis)
    frases_total = sum(l["frases"] for l in respondiveis)
    return {
        f"recall@{k}": round(statistics.mean(l["recall"] for l in respondiveis), 4),
        f"precision@{k}": round(statistics.mean(l["precisao"] for l in respondiveis), 4),
        "mrr": round(statistics.mean(l["rr"] for l in respondiveis), 4),
        f"ndcg@{k}": round(statistics.mean(l["ndcg"] for l in respondiveis), 4),
        "hit@1": round(statistics.mean(1.0 if l["rr"] == 1.0 else 0.0 for l in respondiveis), 4),
        "precisao_citacoes_resposta_local": round(sum(l["citacoes_corretas"] for l in respondiveis) / citacoes, 4) if citacoes else None,
        "literalidade_resposta_local": round(sum(l["frases_literais"] for l in respondiveis) / frases_total, 4) if frases_total else None,
        "fidelidade_resposta_local": _media_ou_none(l["fidelidade_local"] for l in respondiveis
                                                    if l.get("fidelidade_local") is not None),
        "respondiveis_marcadas_sem_evidencia": round(statistics.mean(0.0 if l["evidencia_suficiente"] else 1.0 for l in respondiveis), 4),
        "fora_do_dominio_detectadas": round(statistics.mean(0.0 if l["evidencia_suficiente"] else 1.0 for l in fora), 4) if fora else None,
        "latencia_media_ms": round(1000 * statistics.mean(latencias), 2),
        "latencia_p95_ms": round(1000 * latencias[max(0, math.ceil(0.95 * len(latencias)) - 1)], 2),
        "recall_por_tema": {t: round(statistics.mean(v), 3) for t, v in sorted(temas.items())},
    }


def carregar_respostas(caminho, ids_validos):
    """Lê e valida o JSON de ``--respostas``: lista de {id, resposta, fontes}.

    Levanta ValueError para formato inválido, ids duplicados ou ids fora do gabarito."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(dados, list):
        raise ValueError("Formato inválido em --respostas: esperado uma lista de objetos.")
    respostas = {}
    for n, item in enumerate(dados):
        if not (isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("resposta"), str)
                and isinstance(item.get("fontes"), list) and all(isinstance(f, str) for f in item["fontes"])):
            raise ValueError(f"Formato inválido em --respostas (item {n}): requer id (str), resposta (str) "
                             "e fontes (lista de str).")
        if item["id"] in respostas:
            raise ValueError(f"Ids duplicados em --respostas: {item['id']}")
        respostas[item["id"]] = item
    desconhecidos = sorted(set(respostas) - set(ids_validos))
    if desconhecidos:
        raise ValueError(f"Ids de --respostas fora do gabarito/split avaliado: {', '.join(desconhecidos)}")
    return respostas


def carregar_variantes(caminho, ids_validos):
    """Lê e valida o JSON de ``--variantes``: objeto {id do caso: [pergunta, variante, ...]}.

    Ids ausentes são permitidos (a busca usa só a pergunta); ids fora dos casos avaliados são erro."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(dados, dict):
        raise ValueError("Formato inválido em --variantes: esperado um objeto {id: [consultas]}.")
    for id_caso, lista in dados.items():
        if not (isinstance(lista, list) and lista and all(isinstance(c, str) and c.strip() for c in lista)):
            raise ValueError(f"Formato inválido em --variantes (caso {id_caso}): requer lista não vazia "
                             "de textos não vazios.")
    desconhecidos = sorted(set(dados) - set(ids_validos))
    if desconhecidos:
        raise ValueError(f"Ids de --variantes fora do gabarito/split avaliado: {', '.join(desconhecidos)}")
    return dados


def _gravar_atomico(caminho, dados):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporario, caminho)


def _forma_variantes(dados):
    """Valida o formato {id: [textos não vazios]}; levanta ValueError."""
    if not isinstance(dados, dict):
        raise ValueError("esperado um objeto {id: [consultas]}")
    for id_caso, lista in dados.items():
        if not (isinstance(lista, list) and lista and all(isinstance(c, str) and c.strip() for c in lista)):
            raise ValueError(f"caso {id_caso}: requer lista não vazia de textos não vazios")
    return dados


def ler_variantes_existentes(caminho):
    """Conteúdo já gravado de um arquivo de ``--gerar-variantes`` ({} se não existir).

    Levanta ValueError (arquivo ilegível, JSON corrompido ou formato inesperado)."""
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    try:
        return _forma_variantes(json.loads(caminho.read_text(encoding="utf-8")))
    except (OSError, ValueError) as erro:
        raise ValueError(f"--gerar-variantes: {caminho} está ilegível ou corrompido ({erro}); "
                         "corrija ou apague o arquivo e rode de novo.") from erro


class VariantesInterrompidas(Exception):
    """Disjuntor: muitas falhas seguidas da reescrita; o progresso já foi salvo."""


def _expandir_com_tentativas(pergunta, tentativas, pausa):
    """Resultado de ``expandir_consulta`` ou ``None`` se a LLM falhou em todas as tentativas.

    Falha = o traço registrou um fallback "consulta: ..." (timeout, 429, rede, JSON malformado, sem chave)."""
    for n in range(tentativas):
        traco.iniciar()
        resultado = consulta.expandir_consulta(pergunta)
        if not any(f.startswith("consulta:") for f in traco.coletar()):
            return resultado
        if n + 1 < tentativas:
            time.sleep(pausa)
    return None


def gerar_variantes(casos, caminho, a_cada=20, tentativas=3, pausa=1.0, limite_falhas=5):
    """Chama ``expandir_consulta`` (caminho real, com LLM) para cada caso e grava o JSON de ``--variantes``.

    Retomável: ids já presentes são pulados. Falhas da LLM (após ``tentativas``) NÃO são gravadas, para
    que uma nova execução as repita; ``limite_falhas`` casos seguidos com falha interrompem a geração
    (``VariantesInterrompidas`` após salvar). Grava de forma atômica a cada ``a_cada`` casos novos e, em
    ``finally``, o que faltar (inclusive em Ctrl+C ou erro inesperado). Devolve {"llm", "pendentes",
    "puladas"}; só grava o arquivo se algo novo foi gerado."""
    caminho = Path(caminho)
    dados = ler_variantes_existentes(caminho)
    contagem = {"llm": 0, "pendentes": 0, "puladas": 0}
    nao_salvas = seguidas = 0
    interrompida = False
    try:
        for n, caso in enumerate(casos):
            if caso["id"] in dados:
                contagem["puladas"] += 1
                continue
            if interrompida:
                contagem["pendentes"] += 1
                continue
            resultado = _expandir_com_tentativas(caso["pergunta"], tentativas, pausa)
            if resultado is None:
                contagem["pendentes"] += 1
                seguidas += 1
                interrompida = seguidas >= limite_falhas
                continue
            seguidas = 0
            contagem["llm"] += 1
            dados[caso["id"]] = resultado
            nao_salvas += 1
            if nao_salvas >= a_cada:
                _gravar_atomico(caminho, dados)
                nao_salvas = 0
    finally:
        if nao_salvas:
            _gravar_atomico(caminho, dados)
    contagem["interrompida"] = interrompida
    return contagem


def fidelidade_llm(respostas):
    """Fidelidade das respostas de LLM: média, entre os casos, da média de suporte das frases de cada
    resposta contra suas fontes (cada caso pesa igual; casos sem frases pontuáveis são ignorados).
    Independe do modo de recuperação."""
    por_caso = []
    for r in respostas.values():
        notas = [pontuar_suporte(f, r["fontes"]) for f in frases(r["resposta"])]
        if notas:
            por_caso.append(statistics.mean(notas))
    return _media_ou_none(por_caso)


def filtrar_casos(casos, split):
    """Filtra o gabarito pelo split; casos sem 'split' contam como dev."""
    if split != "todos":
        casos = [c for c in casos if c.get("split", "dev") == split]
    if not casos:
        raise ValueError(f"Nenhum caso no split '{split}'.")
    return casos


def varrer_limiares(positivos, negativos, passos=None):
    """Precisão/revocação/F1 de ``suportada = suporte >= limiar`` para cada limiar.

    ``positivos`` e ``negativos`` são listas de pontuações de suporte; devolve (tabela, melhor),
    com ``melhor`` o limiar de maior F1 (empate: o menor)."""
    passos = passos or [round(0.05 * i, 2) for i in range(1, 20)]
    tabela = []
    for limiar in passos:
        vp = sum(p >= limiar for p in positivos)
        fp = sum(n >= limiar for n in negativos)
        precisao = vp / (vp + fp) if vp + fp else 0.0
        revocacao = vp / len(positivos) if positivos else 0.0
        f1 = 2 * precisao * revocacao / (precisao + revocacao) if precisao + revocacao else 0.0
        tabela.append({"limiar": limiar, "precisao": round(precisao, 4), "revocacao": round(revocacao, 4),
                       "f1": round(f1, 4)})
    return tabela, max(tabela, key=lambda l: (l["f1"], -l["limiar"]))


def calibrar_limiar(casos, k, semente=0, embed=None):
    """Calibra ``RAG_LIMIAR_SUPORTE`` sem LLM.

    Positivos: frases da resposta local de cada caso respondível, contra a fonte citada.
    Negativos: as MESMAS frases contra uma fonte de outro tema (escolha determinística, ``semente``).

    Limitação: calibra só o caminho extrativo (frases literais vs. a própria fonte); o resultado não se
    transfere a respostas livres/parafraseadas da LLM, que exigem recalibração com ``--respostas``."""
    respondiveis = [c for c in casos if c.get("relevantes")]
    recuperados = {}
    for caso in respondiveis:
        recuperados[caso["id"]] = busca_chromadb.buscar_documentos(
            caso["pergunta"], quantidade=k * 2, modo="hibrido")
    # Fontes candidatas a negativas: melhor trecho de cada caso, com o tema do caso.
    pool = [(c["tema"], recuperados[c["id"]][0]["texto"]) for c in respondiveis if recuperados[c["id"]]]
    sorteio = random.Random(semente)
    positivos, negativos = [], []
    for caso in respondiveis:
        secoes = recuperados[caso["id"]][:k]
        local = resposta_local.gerar(caso["pergunta"], secoes)
        outros = [texto for tema, texto in pool if tema != caso["tema"]]
        if not local or not outros:
            continue
        for linha in local["response"].splitlines():
            if not linha.startswith("- ") or "[Fonte " not in linha:
                continue
            numero = int(linha.rsplit("[Fonte ", 1)[1].rstrip("]"))
            frase = linha[2:].rsplit(" [Fonte ", 1)[0]
            positivos.append(pontuar_suporte(frase, [secoes[numero - 1]["texto"]], embed))
            negativos.append(pontuar_suporte(frase, [sorteio.choice(outros)], embed))
    tabela, melhor = varrer_limiares(positivos, negativos)
    return {"positivos": len(positivos), "negativos": len(negativos), "tabela": tabela, "melhor": melhor}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", nargs="*", default=[str(ROOT / "artifacts" / "conhecimento")])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--modos", nargs="+", default=None, metavar="MODO",
                        help="Avalia só estes modos. Combinados: hibrido+reranker+mmr, hibrido+reranker+consulta, "
                             "hibrido+completo (reranker + MMR + reescrita pelas --variantes, ou glossário sem elas).")
    parser.add_argument("--saida", type=Path, default=None, help="Obrigatório, exceto com --calibrar-limiar.")
    parser.add_argument("--calibrar-limiar", action="store_true",
                        help="Em vez da avaliação, varre limiares de suporte (positivos: frases da resposta local; "
                             "negativos: as mesmas frases contra fonte de outro tema) e imprime o melhor por F1. "
                             "Calibra apenas a resposta extrativa; não vale para respostas livres da LLM.")
    parser.add_argument("--split", choices=("dev", "reservado", "todos"), default="todos",
                        help="Subconjunto do gabarito a avaliar (casos sem 'split' contam como dev).")
    parser.add_argument("--respostas", type=Path, default=None,
                        help="JSON com lista de {id, resposta, fontes} para medir a fidelidade de respostas de LLM "
                             "(media por caso da media de suporte das frases; reportada uma vez, fora dos modos).")
    parser.add_argument("--variantes", type=Path, default=None,
                        help="JSON {id do caso: [pergunta, variante, ...]} gravado antes (ver --gerar-variantes); "
                             "habilita o modo hibrido+consulta, reproduzível sem rede.")
    parser.add_argument("--gerar-variantes", type=Path, default=None, metavar="ARQUIVO",
                        help="Chama a reescrita de consulta real (exige NVIDIA_API_KEY) para cada caso do split, "
                             "grava ARQUIVO (retomável) e sai sem avaliar.")
    args = parser.parse_args()
    if args.saida is None and not args.calibrar_limiar and not args.gerar_variantes:
        parser.error("--saida é obrigatório.")
    if args.gerar_variantes and not cliente_llm.configurada():
        parser.error("NVIDIA_API_KEY não configurada")
    if args.gerar_variantes:
        try:
            ler_variantes_existentes(args.gerar_variantes)
        except ValueError as erro:
            parser.error(str(erro))
    casos = json.loads(GABARITO.read_text(encoding="utf-8"))
    try:
        casos = filtrar_casos(casos, args.split)
    except ValueError as erro:
        parser.error(str(erro))
    if args.gerar_variantes:
        contagem = gerar_variantes(casos, args.gerar_variantes)
        print(f"{contagem['llm']} com variantes da LLM, {contagem['pendentes']} pendentes por falha, "
              f"{contagem['puladas']} já presentes de execução anterior; arquivo: {args.gerar_variantes}")
        if contagem["interrompida"]:
            print(f"a API parece indisponível ou sem cota; {contagem['pendentes']} casos pendentes; "
                  "rode novamente para continuar")
        if contagem["pendentes"]:
            raise SystemExit(1)
        return
    respostas = variantes = None
    ids = {c["id"] for c in casos}
    try:
        respostas = carregar_respostas(args.respostas, ids) if args.respostas else None
    except (ValueError, OSError) as erro:  # ValueError: formato/ids; OSError: arquivo ausente ou ilegível
        parser.error(f"--respostas: {erro}" if isinstance(erro, OSError) else str(erro))
    try:
        variantes = carregar_variantes(args.variantes, ids) if args.variantes else None
    except json.JSONDecodeError as erro:
        parser.error(f"--variantes: JSON inválido ({erro})")
    except OSError as erro:
        parser.error(f"--variantes: {erro}")
    except ValueError as erro:
        parser.error(str(erro))
    documentos = carregar_corpus(args.snapshots)
    chunks, vetores, tempo_embed, modelo = preparar(documentos)
    colecao = ColecaoMemoria(chunks, vetores)

    from .configuracao import MODELO_EMBEDDINGS, prefixo

    def embed(textos):
        marca = prefixo("consulta", MODELO_EMBEDDINGS)
        return modelo.encode([marca + t for t in textos], normalize_embeddings=True, convert_to_numpy=True,
                             show_progress_bar=False).tolist()

    busca_chromadb.obter_colecao = lambda: colecao
    busca_chromadb.criar_embeddings = lambda: embed
    if args.calibrar_limiar:
        calibracao = calibrar_limiar(casos, args.k)
        for linha in calibracao["tabela"]:
            print(json.dumps(linha))
        print("melhor", json.dumps(calibracao["melhor"]), "positivos", calibracao["positivos"],
              "negativos", calibracao["negativos"])
        if args.saida:
            args.saida.parent.mkdir(parents=True, exist_ok=True)
            args.saida.write_text(json.dumps({**calibracao, "split": args.split, "k": args.k},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        return
    relatorio = {"gerado_em": datetime.now(timezone.utc).isoformat(), "k": args.k, "split": args.split,
                 "corpus": {"documentos": len(documentos), "trechos": len(chunks),
                            "internos": sum(d.get("origem_conhecimento") == "qone_interno" for d in documentos),
                            "tempo_embeddings_s": round(tempo_embed, 1)},
                 "casos": {"respondiveis": sum(1 for c in casos if c.get("relevantes")),
                           "fora_do_dominio": sum(1 for c in casos if not c.get("relevantes"))},
                 "fidelidade_llm": fidelidade_llm(respostas) if respostas else None,
                 "modos": {}, "detalhes": {}}
    modos = ["linha_de_base", "vetorial", "lexical", "hibrido"]
    if reranker.disponivel():  # sem modelo local o modo seria idêntico ao híbrido e enganaria a comparação
        modos.append("hibrido+reranker")
    else:
        print("hibrido+reranker ignorado: defina RAG_RERANKER_MODEL com um cross-encoder disponível localmente.")
    modos.append("hibrido+mmr")
    modos.append("hibrido+consulta_local")
    if variantes is not None:
        modos.append("hibrido+consulta")
    if args.modos:  # só os modos pedidos (inclui os combinados, que não rodam por padrão)
        desconhecidos = [m for m in args.modos if m not in TODOS_OS_MODOS]
        if desconhecidos:
            parser.error(f"modos desconhecidos: {', '.join(desconhecidos)}")
        sem_modelo = [m for m in args.modos if m in MODOS_COM_RERANKER and not reranker.disponivel()]
        if sem_modelo:
            parser.error(f"{', '.join(sem_modelo)} exigem RAG_RERANKER_MODEL com o modelo disponível localmente.")
        modos = list(args.modos)
    for modo in modos:
        modo_busca, variaveis = _modo_de_busca(modo)
        with ambiente(**variaveis):
            busca_chromadb.buscar_documentos("aquecimento", modo=modo_busca)
        linhas = avaliar_modo(modo, casos, args.k, variantes=variantes)
        relatorio["modos"][modo] = resumir(linhas, args.k)
        relatorio["detalhes"][modo] = linhas
        print(modo, json.dumps(relatorio["modos"][modo], ensure_ascii=False))
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
