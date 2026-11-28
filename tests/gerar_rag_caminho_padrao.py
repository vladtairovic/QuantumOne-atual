"""Gera ``tests/fixtures/rag_caminho_padrao.json``: saídas de referência do caminho PADRÃO do RAG.

O arquivo foi gerado UMA vez a partir do código anterior à branch ``feat/rag-profissional``
(commit ``e414f88``) e é usado por ``tests/test_rag_caminho_padrao.py`` para garantir que,
com todas as camadas ``RAG_*`` desligadas, o código atual devolve exatamente o mesmo.

Regenerar (só quando o comportamento padrão mudar de propósito):

    git archive e414f88 rag | tar -x -C <pasta-vazia>
    python tests/gerar_rag_caminho_padrao.py --raiz <pasta-vazia>            # reaproveita o corpus do arquivo
    python tests/gerar_rag_caminho_padrao.py --raiz <pasta-vazia> --novo-corpus   # sorteia outro corpus

``--raiz`` é a pasta que contém o pacote ``rag`` a medir (sem ela, usa o repositório atual).
O embedder é uma função pura do texto (bolsa de palavras com hash), sem modelo nem rede.
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from unittest import mock

AQUI = Path(__file__).resolve().parent
ARQUIVO = AQUI / "fixtures" / "rag_caminho_padrao.json"
DIMENSOES = 64
MODOS = ("hibrido", "vetorial", "lexical")
QUANTIDADES = (3, 10)
LIMITES = (500, 1500, 6000)


def embedar(texto):
    """Vetor normalizado, função pura do texto (bolsa de palavras com hash)."""
    vetor = [0.0] * DIMENSOES
    for palavra in re.findall(r"\w+", texto.lower()):
        resumo = hashlib.md5(palavra.encode("utf-8")).digest()
        vetor[resumo[0] % DIMENSOES] += 1.0 if resumo[1] % 2 else -1.0
    norma = math.sqrt(sum(v * v for v in vetor)) or 1.0
    return [v / norma for v in vetor]


def novo_corpus(perguntas):
    sorteio = random.Random(20261127)
    ruido = ("circuito porta qubit medição estado backend simulador portas gate hardware ruído erro amplitude "
             "fase transpile camada otimização registro clássico quântico observável medir resultado execução "
             "job sampler estimator parâmetro vetor matriz operador").split()
    documentos = []
    for n, pergunta in enumerate(perguntas):
        palavras = re.findall(r"\w+", pergunta.lower())
        chunks = []
        for indice in range(sorteio.choice((1, 2, 3))):
            frases = []
            for _ in range(sorteio.choice((2, 3, 4))):
                base = sorteio.sample(palavras, min(len(palavras), 5)) + sorteio.sample(ruido, 7)
                sorteio.shuffle(base)
                frases.append(" ".join(base).capitalize() + ".")
            chunks.append(" ".join(frases))
        documentos.append({"document_id": f"doc_{n:02d}", "titulo": f"Tópico {n}", "chunks": chunks})
    for n in range(len(perguntas), len(perguntas) + 10):
        chunks = [" ".join(sorteio.sample(ruido, 12)).capitalize() + "." for _ in range(sorteio.choice((1, 2)))]
        documentos.append({"document_id": f"doc_{n:02d}", "titulo": f"Tópico {n}", "chunks": chunks})
    return documentos


def chunks_do_corpus(corpus):
    saida = []
    for documento in corpus:
        for indice, texto in enumerate(documento["chunks"]):
            saida.append({"id": f"{documento['document_id']}#{indice}", "texto": texto, "metadados": {
                "document_id": documento["document_id"], "secao": f"Seção {indice}",
                "fonte": f"https://exemplo.test/{documento['document_id']}", "titulo": documento["titulo"],
                "indice": indice, "versao_documentacao": "v", "coletado_em": "c"}})
    return saida


def listas_de_contexto(chunks):
    def doc(c, pontuacao=None):
        d = {"id": c["id"], "texto": c["texto"], "metadados": c["metadados"]}
        if pontuacao is not None:
            d["pontuacao"] = pontuacao
        return d
    return {
        "com_pontuacao": [doc(c, round(0.03 - 0.004 * n, 6)) for n, c in enumerate(chunks[:8])],
        "sem_pontuacao": [doc(c) for c in chunks[8:14]],
        "um_documento": [doc(chunks[20], 0.03)],
        "textos_longos": [doc({**c, "texto": (c["texto"] + " ") * 12}, round(0.03 - 0.002 * n, 6))
                          for n, c in enumerate(chunks[:5])],
    }


def calcular(corpus, perguntas):
    """Executa o código do pacote ``rag`` importado, sem camadas RAG_* e sem rede."""
    limpo = {n: v for n, v in os.environ.items() if not n.startswith("RAG_")}
    with mock.patch.dict(os.environ, limpo, clear=True):
        return _calcular(corpus, perguntas)


def _calcular(corpus, perguntas):
    from rag import avaliacao_rag, busca_chromadb
    from rag.contexto import preparar_contexto
    chunks = chunks_do_corpus(corpus)
    colecao = avaliacao_rag.ColecaoMemoria(chunks, [embedar(c["texto"]) for c in chunks])
    busca_chromadb.obter_colecao = lambda: colecao
    busca_chromadb.criar_embeddings = lambda: lambda textos: [embedar(t) for t in textos]
    busca_chromadb.invalidar_indice_lexical() if hasattr(busca_chromadb, "invalidar_indice_lexical") else None
    busca = {}
    for pergunta in perguntas:
        for modo in MODOS:
            for quantidade in QUANTIDADES:
                resultado = busca_chromadb.buscar_documentos(pergunta, quantidade=quantidade, modo=modo)
                busca[f"{modo}|{quantidade}|{pergunta}"] = [[d["id"], round(d["pontuacao"], 6)] for d in resultado]
    contexto = {}
    for nome, lista in listas_de_contexto(chunks).items():
        for limite in LIMITES:
            texto, referencias = preparar_contexto(lista, limite=limite)
            contexto[f"{nome}|{limite}"] = {"contexto": texto, "referencias": referencias}
    return busca, contexto


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raiz", type=Path, default=AQUI.parent)
    parser.add_argument("--novo-corpus", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.raiz))
    import rag
    assert Path(rag.__file__).resolve().is_relative_to(args.raiz.resolve()), rag.__file__
    if args.novo_corpus or not ARQUIVO.exists():
        casos = json.loads((AQUI / "rag_benchmark.json").read_text(encoding="utf-8"))
        perguntas = [c["pergunta"] for c in casos][:20]
        corpus = novo_corpus(perguntas)
    else:
        antigo = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        perguntas, corpus = antigo["perguntas"], antigo["corpus"]
    busca, contexto = calcular(corpus, perguntas)
    ARQUIVO.parent.mkdir(exist_ok=True)
    ARQUIVO.write_text(json.dumps({"perguntas": perguntas, "corpus": corpus, "busca": busca,
                                   "contexto": contexto}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{ARQUIVO} ({ARQUIVO.stat().st_size} bytes) gerado com {args.raiz}")


if __name__ == "__main__":
    main()
