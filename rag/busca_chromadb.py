"""Recuperação híbrida na coleção local do ChromaDB.

Etapas (cada uma medida em ``tempos``):
1. Busca semântica vetorial no ChromaDB (embeddings multilíngues).
2. Busca lexical BM25 em memória sobre os mesmos trechos, com tokenização
   sensível a código e expansão PT->EN de termos técnicos (``glossario``).
3. Fusão por Reciprocal Rank Fusion (RRF).
4. Reordenação leve: correspondência exata de identificadores de código,
   preferência pela biblioteca detectada e penalidade para conteúdo marcado
   como desatualizado.
5. Agrupamento por seção, reconstituindo a ordem original dos trechos.

``RAG_MODO`` escolhe ``hibrido`` (padrão), ``vetorial`` ou ``lexical``; os
três modos existem para a avaliação comparativa em ``rag/avaliacao_rag.py``.
Nenhuma etapa acessa a internet.
"""
import math
import os
import re
import threading
from collections import Counter, defaultdict
from time import perf_counter

from .banco import obter_colecao
from .configuracao import criar_embeddings
from .glossario import expandir
from .texto import termos_unicos, tokens

RRF_K = 60
_CACHE = {"chave": None, "indice": None}
_CACHE_LOCK = threading.Lock()
_VERSAO = [0]
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|[a-z]+_[a-z0-9_]+|[A-Z][a-z]+[A-Z][A-Za-z]+")


def invalidar_indice_lexical():
    """Chamado após gravações para que o BM25 reflita a coleção."""
    with _CACHE_LOCK:
        _VERSAO[0] += 1


class IndiceBM25:
    def __init__(self, registros, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.ids, self.metadados, self.textos, self.comprimentos = [], [], [], []
        self.invertido = defaultdict(dict)
        for identificador, texto, meta in registros:
            toks = tokens(texto)
            indice = len(self.ids)
            self.ids.append(identificador)
            self.metadados.append(meta)
            self.textos.append(texto)
            self.comprimentos.append(len(toks))
            for termo, freq in Counter(toks).items():
                self.invertido[termo][indice] = freq
        self.n = len(self.ids)
        self.media = (sum(self.comprimentos) / self.n) if self.n else 0.0

    def buscar(self, termos, quantidade, pesos=None):
        pontos = defaultdict(float)
        for termo in termos:
            postings = self.invertido.get(termo)
            if not postings:
                continue
            idf = math.log(1 + (self.n - len(postings) + 0.5) / (len(postings) + 0.5))
            peso = (pesos or {}).get(termo, 1.0)
            for indice, freq in postings.items():
                norma = self.k1 * (1 - self.b + self.b * self.comprimentos[indice] / (self.media or 1))
                pontos[indice] += peso * idf * freq * (self.k1 + 1) / (freq + norma)
        melhores = sorted(pontos.items(), key=lambda item: -item[1])[:quantidade]
        return [(self.ids[i], self.metadados[i], self.textos[i], nota) for i, nota in melhores]


def _ler_registros(colecao):
    registros, offset = [], 0
    while True:
        pagina = colecao.get(limit=512, offset=offset, include=["documents", "metadatas"])
        ids = pagina["ids"]
        registros.extend(zip(ids, pagina["documents"], pagina["metadatas"]))
        if len(ids) < 512:
            return registros
        offset += 512


def indice_lexical(colecao, total):
    chave = (id(colecao), total, _VERSAO[0])
    with _CACHE_LOCK:
        if _CACHE["chave"] == chave:
            return _CACHE["indice"]
    indice = IndiceBM25(_ler_registros(colecao))
    with _CACHE_LOCK:
        _CACHE.update(chave=chave, indice=indice)
    return indice


def _identificadores(pergunta):
    return {m.group(0) for m in _IDENT.finditer(pergunta)}


def _modo():
    modo = os.getenv("RAG_MODO", "hibrido").strip().lower()
    return modo if modo in ("hibrido", "vetorial", "lexical") else "hibrido"


def buscar_documentos(pergunta, quantidade=3, *, tempos=None, modo=None, biblioteca=None, reordenar=True):
    medidas = tempos if tempos is not None else {}
    medidas.clear()
    inicio_total = perf_counter()
    if not pergunta.strip():
        return []
    if quantidade < 1:
        raise ValueError("A quantidade deve ser pelo menos 1.")
    modo = modo or _modo()

    inicio = perf_counter()
    colecao = obter_colecao()
    medidas["Obter coleção (inclui inicialização se necessária)"] = perf_counter() - inicio
    inicio = perf_counter()
    total = colecao.count()
    medidas["Contar registros"] = perf_counter() - inicio
    if total == 0:
        return []

    candidatos = min(total, max(20, quantidade * 6))
    metadados_por_id, textos_por_id, rank_vetorial, rank_lexical, distancias = {}, {}, {}, {}, {}
    limite = os.getenv("RAG_MAX_DISTANCE", "").strip()

    if modo in ("hibrido", "vetorial"):
        inicio = perf_counter()
        vetores = criar_embeddings()([pergunta])
        medidas["Gerar embedding da pergunta"] = perf_counter() - inicio
        inicio = perf_counter()
        resultado = colecao.query(query_embeddings=vetores, n_results=candidatos,
                                  include=["documents", "metadatas", "distances"])
        medidas["Buscar vetores no ChromaDB"] = perf_counter() - inicio
        documentos_vetor = (resultado.get("documents") or [[]])[0] or [None] * len(resultado["ids"][0])
        for posicao, (identificador, meta, distancia, texto) in enumerate(zip(
                resultado["ids"][0], resultado["metadatas"][0], resultado["distances"][0], documentos_vetor)):
            # A distância l2 de vetores normalizados não é probabilidade/confiança.
            if limite and distancia > float(limite):
                continue
            rank_vetorial[identificador] = posicao + 1
            metadados_por_id[identificador] = meta
            textos_por_id[identificador] = texto or ""
            distancias[identificador] = float(distancia)

    originais = termos_unicos(pergunta)
    extras = expandir(originais)
    if modo in ("hibrido", "lexical"):
        inicio = perf_counter()
        indice = indice_lexical(colecao, total)
        medidas["Preparar índice lexical BM25"] = perf_counter() - inicio
        inicio = perf_counter()
        # Termos expandidos pesam menos que os digitados pelo usuário.
        pesos = {**{t: 0.6 for t in extras}, **{t: 1.0 for t in originais}}
        for posicao, (identificador, meta, texto, _) in enumerate(
                indice.buscar(originais + extras, candidatos, pesos)):
            rank_lexical[identificador] = posicao + 1
            metadados_por_id.setdefault(identificador, meta)
            if texto:
                textos_por_id[identificador] = texto
        medidas["Buscar termos (BM25)"] = perf_counter() - inicio

    inicio = perf_counter()
    identificadores = _identificadores(pergunta)
    menciona_migracao = bool({"deprecated", "removed", "migration", "migracao", "obsoleto",
                              "removido"} & set(originais + extras))
    pontuacao = {}
    termos_consulta = set(originais + extras)
    for identificador, meta in metadados_por_id.items():
        if not reordenar:
            # Linha de base da avaliação: ordem vetorial pura, como antes da reestruturação.
            pontuacao[identificador] = 1.0 / (RRF_K + rank_vetorial.get(identificador, 10 ** 6))
            continue
        nota = 0.0
        if identificador in rank_vetorial:
            nota += 1.0 / (RRF_K + rank_vetorial[identificador])
        if identificador in rank_lexical:
            nota += 1.0 / (RRF_K + rank_lexical[identificador])
        if biblioteca and meta.get("biblioteca") == biblioteca:
            nota *= 1.15
        if meta.get("desatualizado") and not menciona_migracao:
            nota *= 0.5
        # Título da página ou seção nomeia o conceito pedido (ex.: "Operator", "Statevector").
        if termos_consulta & set(tokens(f"{meta.get('titulo', '')} {meta.get('secao', '')}")):
            nota *= float(os.getenv("RAG_BONUS_TITULO", "1.2"))
        # Identificadores de código citados literalmente na pergunta.
        if identificadores and any(i in textos_por_id.get(identificador, "") for i in identificadores):
            nota *= 1.25
        pontuacao[identificador] = nota
    ordenados = sorted(pontuacao, key=lambda i: -pontuacao[i])
    medidas["Fusão RRF e reordenação"] = perf_counter() - inicio

    inicio_contexto = perf_counter()
    documentos, secoes_recuperadas = [], set()
    for identificador in ordenados:
        if len(documentos) >= quantidade:
            break
        metadados = metadados_por_id[identificador]
        document_id = metadados["document_id"]
        secao = metadados["secao"]
        chave = (document_id, secao)
        # Evita devolver a mesma seção mais de uma vez.
        if chave in secoes_recuperadas:
            continue
        secoes_recuperadas.add(chave)
        registros = colecao.get(where={"$and": [{"document_id": document_id}, {"secao": secao}]},
                                include=["documents", "metadatas"])
        # Reconstitui a ordem original dos trechos.
        partes = sorted(zip(registros["ids"], registros["documents"], registros["metadatas"]),
                        key=lambda item: item[2]["indice"])
        prefixo = secao + "\n\n"
        textos = [texto.removeprefix(prefixo) for _, texto, _ in partes]
        documentos.append({
            "id": identificador,
            "ids_contexto": [item[0] for item in partes],
            "texto": prefixo + "\n\n".join(textos),
            "metadados": {
                "document_id": document_id,
                "secao": secao,
                "fonte": metadados["fonte"],
                "titulo": metadados["titulo"],
                "versao_documentacao": metadados["versao_documentacao"],
                "coletado_em": metadados["coletado_em"],
                "origem_conhecimento": metadados.get("origem_conhecimento", "documentacao_externa"),
                "biblioteca": metadados.get("biblioteca"),
                "desatualizado": bool(metadados.get("desatualizado", False)),
            },
            # Distância do chunk que levou à seção (ausente se veio só do BM25).
            "distancia": distancias.get(identificador),
            "pontuacao": round(pontuacao[identificador], 6),
            "ranks": {"vetorial": rank_vetorial.get(identificador), "lexical": rank_lexical.get(identificador)},
        })
    medidas["Recuperar e montar seções"] = perf_counter() - inicio_contexto
    medidas["TOTAL"] = perf_counter() - inicio_total
    return documentos


def avaliar_evidencia(pergunta, documentos, minimo=None):
    """Mede se os trechos recuperados cobrem os conceitos da pergunta.

    Cada termo da pergunta conta como coberto quando ele, ou um equivalente do
    glossário, aparece nos trechos. Recuperar *algo* não basta: abaixo do
    mínimo, a resposta deve declarar contexto insuficiente.
    """
    minimo = float(os.getenv("RAG_MIN_COBERTURA", "0.34")) if minimo is None else minimo
    originais = termos_unicos(pergunta)
    if not originais:
        return {"suficiente": False, "cobertura": 0.0, "motivo": "Pergunta sem termos pesquisáveis."}
    if not documentos:
        return {"suficiente": False, "cobertura": 0.0, "motivo": "Nenhum trecho recuperado."}
    vocab = set()
    for documento in documentos:
        vocab.update(tokens(documento.get("texto", "")))
    cobertos = [t for t in originais if t in vocab or any(e in vocab for e in expandir([t]))]
    cobertura = len(cobertos) / len(originais)
    faltantes = [t for t in originais if t not in cobertos]
    suficiente = cobertura >= minimo
    return {"suficiente": suficiente, "cobertura": round(cobertura, 3),
            "termos_sem_evidencia": faltantes[:12],
            "motivo": None if suficiente else
            "Os trechos recuperados não cobrem os principais termos da pergunta."}


if __name__ == "__main__":
    print("Conectando ao servidor ChromaDB...", flush=True)
    obter_colecao()
    print("Digite 'sair' para encerrar. A primeira consulta pode incluir aquecimento.")
    while True:
        pergunta = input("\nSua pergunta: ").strip()
        if pergunta.lower() == "sair":
            break
        if not pergunta:
            continue
        tempos = {}
        resultados = buscar_documentos(pergunta, tempos=tempos)
        print("\nTEMPOS (sem incluir a impressão dos resultados):")
        for etapa, duracao in tempos.items():
            print(f"  {etapa}: {duracao:.3f} s")
        for posicao, documento in enumerate(resultados, start=1):
            print(f"\nResultado {posicao}")
            print("Seção:", documento["metadados"]["secao"])
            print("ID encontrado:", documento["id"])
            print("Pontuação:", documento["pontuacao"], "| ranks:", documento["ranks"])
            print("Prévia:", documento["texto"][:600])
            print("Fonte:", documento["metadados"]["fonte"])
