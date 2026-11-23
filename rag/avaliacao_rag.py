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
        --saida docs/benchmarks/rag-avaliacao.json
"""
import argparse
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import busca_chromadb, conhecimento_interno, resposta_local
from .preparar_chunks import gerar_chunks
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
        return {"ids": [ids], "metadatas": [[self.metas[i] for i in ids]],
                "documents": [[self.textos[i] for i in ids]], "distances": [[float(dist[i]) for i in ordem]]}

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
    from .configuracao import MODELO_EMBEDDINGS, obter_modelo, limite_tokens
    modelo = obter_modelo()
    chunks = []
    for documento in documentos:
        documento.setdefault("versao_documentacao", "snapshot")
        documento.setdefault("coletado_em", "snapshot")
        chunks.extend(gerar_chunks(documento, modelo.tokenizer, limite_tokens(), MODELO_EMBEDDINGS))
    inicio = time.perf_counter()
    vetores = modelo.encode([c["texto"] for c in chunks], normalize_embeddings=True, convert_to_numpy=True,
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


def avaliar_modo(modo, casos, k):
    linhas = []
    for caso in casos:
        tempos = {}
        inicio = time.perf_counter()
        secoes = busca_chromadb.buscar_documentos(
            caso["pergunta"], quantidade=k * 2, tempos=tempos, modo="vetorial" if modo == "linha_de_base" else modo,
            reordenar=modo != "linha_de_base")
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
            linha.update(recall=r, precisao=p, rr=rr, ndcg=ndcg, citacoes=citacoes,
                         citacoes_corretas=corretas, frases_literais=literais, frases=total_frases,
                         primeiro_relevante=next((d for d in docs if d in relevantes), None))
        linhas.append(linha)
    return linhas


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
        "respondiveis_marcadas_sem_evidencia": round(statistics.mean(0.0 if l["evidencia_suficiente"] else 1.0 for l in respondiveis), 4),
        "fora_do_dominio_detectadas": round(statistics.mean(0.0 if l["evidencia_suficiente"] else 1.0 for l in fora), 4) if fora else None,
        "latencia_media_ms": round(1000 * statistics.mean(latencias), 2),
        "latencia_p95_ms": round(1000 * latencias[max(0, math.ceil(0.95 * len(latencias)) - 1)], 2),
        "recall_por_tema": {t: round(statistics.mean(v), 3) for t, v in sorted(temas.items())},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", nargs="*", default=[str(ROOT / "artifacts" / "conhecimento")])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    documentos = carregar_corpus(args.snapshots)
    chunks, vetores, tempo_embed, modelo = preparar(documentos)
    colecao = ColecaoMemoria(chunks, vetores)

    def embed(textos):
        return modelo.encode(textos, normalize_embeddings=True, convert_to_numpy=True,
                             show_progress_bar=False).tolist()

    busca_chromadb.obter_colecao = lambda: colecao
    busca_chromadb.criar_embeddings = lambda: embed
    casos = json.loads(GABARITO.read_text(encoding="utf-8"))
    relatorio = {"gerado_em": datetime.now(timezone.utc).isoformat(), "k": args.k,
                 "corpus": {"documentos": len(documentos), "trechos": len(chunks),
                            "internos": sum(d.get("origem_conhecimento") == "qone_interno" for d in documentos),
                            "tempo_embeddings_s": round(tempo_embed, 1)},
                 "casos": {"respondiveis": sum(1 for c in casos if c.get("relevantes")),
                           "fora_do_dominio": sum(1 for c in casos if not c.get("relevantes"))},
                 "modos": {}, "detalhes": {}}
    for modo in ("linha_de_base", "vetorial", "lexical", "hibrido"):
        busca_chromadb.buscar_documentos("aquecimento", modo="vetorial" if modo == "linha_de_base" else modo)
        linhas = avaliar_modo(modo, casos, args.k)
        relatorio["modos"][modo] = resumir(linhas, args.k)
        relatorio["detalhes"][modo] = linhas
        print(modo, json.dumps(relatorio["modos"][modo], ensure_ascii=False))
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
