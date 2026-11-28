"""Recuperação híbrida na coleção local do ChromaDB.

Etapas (cada uma medida em ``tempos``):
1. Busca semântica vetorial no ChromaDB (embeddings multilíngues).
2. Busca lexical BM25 em memória sobre os mesmos trechos, com tokenização
   sensível a código e expansão PT->EN de termos técnicos (``glossario``).
3. Fusão por Reciprocal Rank Fusion (RRF).
4. Reordenação leve: correspondência exata de identificadores de código,
   preferência pela biblioteca detectada e penalidade para conteúdo marcado
   como desatualizado.
5. Opcional (``RAG_RERANKER=1``): reranker neural sobre os 30 primeiros
   pós-RRF, misturado à nota RRF antes dos multiplicadores (ver ``reranker``).
6. Opcional (``RAG_MMR=1``): MMR reordena a cabeça (``RAG_MMR_CANDIDATOS``, padrão 20)
   por diversidade (``RAG_MMR_LAMBDA``, padrão 0.7); só muda a ordem, nunca a pontuação.
7. Agrupamento por seção, reconstituindo a ordem original dos trechos.

``RAG_MODO`` escolhe ``hibrido`` (padrão), ``vetorial`` ou ``lexical``; os
três modos existem para a avaliação comparativa em ``rag/avaliacao_rag.py``.
Nenhuma etapa acessa a internet.
"""
import logging
import math
import os
import re
import threading
from collections import Counter, defaultdict
from time import perf_counter

from . import diversidade, recursos, reranker, traco
from .banco import obter_colecao
from .configuracao import criar_embeddings
from .glossario import expandir
from .texto import termos_unicos, tokens

RRF_K = 60
log = logging.getLogger(__name__)
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


def _normalizar(valores):
    menor, maior = min(valores), max(valores)
    amplitude = maior - menor
    return [(v - menor) / amplitude if amplitude else 0.0 for v in valores]


def _peso_reranker(padrao=0.7):
    try:
        peso = float(os.getenv("RAG_RERANKER_PESO", str(padrao)))
    except ValueError:
        return padrao
    return min(1.0, max(0.0, peso)) if math.isfinite(peso) else padrao


def _topo_reranker(padrao=30, minimo=5, maximo=100):
    try:
        topo = int(os.getenv("RAG_RERANKER_TOPO", "").strip() or padrao)
    except ValueError:
        return padrao
    return min(maximo, max(minimo, topo))


def _aplicar_reranker(pergunta, ordenados, textos_por_id, base_rrf, multiplicador, pontuacao, topo=30):
    """Reordena os ``topo`` primeiros pós-RRF; atualiza ``pontuacao`` e devolve a nova ordem.

    Os reordenados vêm antes dos demais, que mantêm a ordem anterior.
    Sem modelo, ``reranker.reordenar`` devolve a entrada e nada muda."""
    candidatos = [{"id": i, "texto": textos_por_id.get(i, ""), "nota": base_rrf[i]} for i in ordenados]
    resultado = reranker.reordenar(pergunta, candidatos, topo=topo)
    reordenados = [c for c in resultado if "nota_reranker" in c]
    if not reordenados:
        return ordenados
    peso = _peso_reranker()
    neurais = _normalizar([c["nota_reranker"] for c in reordenados])
    rrfs = _normalizar([c["nota"] for c in reordenados])
    menor, maior = min(c["nota"] for c in reordenados), max(c["nota"] for c in reordenados)
    for c, neural, rrf in zip(reordenados, neurais, rrfs):
        # Volta à escala RRF da própria cabeça antes dos multiplicadores de regra.
        mistura = peso * neural + (1 - peso) * rrf
        pontuacao[c["id"]] = (menor + mistura * (maior - menor)) * multiplicador[c["id"]]
    cabeca = sorted((c["id"] for c in reordenados), key=lambda i: -pontuacao[i])
    escolhidos = set(cabeca)
    return cabeca + [i for i in ordenados if i not in escolhidos]


def _numero_env(nome, padrao, convert, minimo, maximo=None):
    try:
        valor = convert(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao
    if not math.isfinite(valor) or valor < minimo or (maximo is not None and valor > maximo):
        return padrao
    return valor


def _aplicar_mmr(ordenados, pontuacao, vetores, ha_vetorial):
    """Reordena a cabeça de ``ordenados`` por MMR; devolve a ordem original em qualquer falha.

    Só entram candidatos com embedding (vindos da busca vetorial); os achados apenas
    pelo BM25 não têm vetor e seguem depois da cabeça, na ordem anterior."""
    try:
        if ha_vetorial and not vetores:
            raise ValueError("a coleção não devolveu embeddings")
        cabeca = [i for i in ordenados if i in vetores][:_numero_env("RAG_MMR_CANDIDATOS", 20, int, 1)]
        if len(cabeca) < 2:
            return ordenados
        lambda_ = _numero_env("RAG_MMR_LAMBDA", 0.7, float, 0.0, 1.0)
        nova = diversidade.mmr([(i, vetores[i], pontuacao[i]) for i in cabeca], lambda_=lambda_, k=len(cabeca))
        escolhidos = set(nova)
        return nova + [i for i in ordenados if i not in escolhidos]
    except Exception as erro:  # a camada é opcional: nunca derruba a busca
        log.warning("MMR ignorado, mantida a ordem original: %s", erro)
        traco.registrar_fallback(f"mmr: {type(erro).__name__}")
        return ordenados


def _gerar_candidatos(colecao, total, consulta, modo, candidatos, limite, medidas,
                      metadados_por_id, textos_por_id, distancias, vetores_por_id=None):
    """Candidatos de uma consulta: ranks vetorial e lexical (1 = melhor).

    Acumula metadados, textos e distâncias nos dicionários recebidos."""
    rank_vetorial, rank_lexical = {}, {}
    if modo in ("hibrido", "vetorial"):
        inicio = perf_counter()
        vetores = criar_embeddings()([consulta])
        _somar(medidas, "Gerar embedding da pergunta", perf_counter() - inicio)
        inicio = perf_counter()
        incluir = ["documents", "metadatas", "distances"] + (["embeddings"] if vetores_por_id is not None else [])
        resultado = colecao.query(query_embeddings=vetores, n_results=candidatos, include=incluir)
        _somar(medidas, "Buscar vetores no ChromaDB", perf_counter() - inicio)
        documentos_vetor = (resultado.get("documents") or [[]])[0] or [None] * len(resultado["ids"][0])
        embeddings = resultado.get("embeddings") if vetores_por_id is not None else None
        embeddings = None if embeddings is None else embeddings[0]
        for posicao, (identificador, meta, distancia, texto) in enumerate(zip(
                resultado["ids"][0], resultado["metadatas"][0], resultado["distances"][0], documentos_vetor)):
            # A distância l2 de vetores normalizados não é probabilidade/confiança.
            if limite and distancia > float(limite):
                continue
            rank_vetorial[identificador] = posicao + 1
            metadados_por_id[identificador] = meta
            textos_por_id[identificador] = texto or ""
            distancias[identificador] = min(float(distancia), distancias.get(identificador, float("inf")))
            if embeddings is not None:
                vetores_por_id[identificador] = [float(x) for x in embeddings[posicao]]
    originais = termos_unicos(consulta)
    extras = expandir(originais)
    if modo in ("hibrido", "lexical"):
        inicio = perf_counter()
        indice = indice_lexical(colecao, total)
        _somar(medidas, "Preparar índice lexical BM25", perf_counter() - inicio)
        inicio = perf_counter()
        # Termos expandidos pesam menos que os digitados pelo usuário.
        pesos = {**{t: 0.6 for t in extras}, **{t: 1.0 for t in originais}}
        for posicao, (identificador, meta, texto, _) in enumerate(
                indice.buscar(originais + extras, candidatos, pesos)):
            rank_lexical[identificador] = posicao + 1
            metadados_por_id.setdefault(identificador, meta)
            if texto:
                textos_por_id[identificador] = texto
        _somar(medidas, "Buscar termos (BM25)", perf_counter() - inicio)
    return rank_vetorial, rank_lexical


def _somar(medidas, etapa, duracao):
    medidas[etapa] = medidas.get(etapa, 0.0) + duracao


def buscar_documentos(pergunta, quantidade=3, *, tempos=None, modo=None, biblioteca=None, reordenar=True,
                      consultas=None):
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
    metadados_por_id, textos_por_id, distancias = {}, {}, {}
    limite = os.getenv("RAG_MAX_DISTANCE", "").strip()
    originais = termos_unicos(pergunta)
    extras = expandir(originais)

    usar_mmr = reordenar and modo in ("hibrido", "vetorial") and recursos.ativo("mmr")
    vetores_por_id = {} if usar_mmr else None  # embeddings só são pedidos quando o MMR está ligado
    fusao = None
    todas = list(dict.fromkeys(c for c in [pergunta, *(consultas or [])] if c and c.strip()))
    if len(todas) <= 1:
        rank_vetorial, rank_lexical = _gerar_candidatos(
            colecao, total, pergunta, modo, candidatos, limite, medidas,
            metadados_por_id, textos_por_id, distancias, vetores_por_id)
    else:
        # Várias consultas: cada uma gera seus candidatos e os rankings são fundidos
        # por RRF antes da reordenação (a consulta original segue guiando as regras).
        rank_vetorial, rank_lexical, fusao = {}, {}, defaultdict(float)
        for consulta in todas:
            rv, rl = _gerar_candidatos(colecao, total, consulta, modo, candidatos, limite, medidas,
                                       metadados_por_id, textos_por_id, distancias, vetores_por_id)
            for destino, ranks in ((rank_vetorial, rv), (rank_lexical, rl)):
                for identificador, posicao in ranks.items():
                    fusao[identificador] += 1.0 / (RRF_K + posicao)
                    destino[identificador] = min(posicao, destino.get(identificador, posicao))

    inicio = perf_counter()
    identificadores = _identificadores(pergunta)
    menciona_migracao = bool({"deprecated", "removed", "migration", "migracao", "obsoleto",
                              "removido"} & set(originais + extras))
    pontuacao, base_rrf, multiplicador = {}, {}, {}
    termos_consulta = set(originais + extras)
    for identificador, meta in metadados_por_id.items():
        if not reordenar:
            # Linha de base da avaliação: ordem vetorial pura, como antes da reestruturação.
            pontuacao[identificador] = 1.0 / (RRF_K + rank_vetorial.get(identificador, 10 ** 6))
            continue
        nota = 0.0
        if fusao is not None:
            nota = fusao[identificador]
        else:
            if identificador in rank_vetorial:
                nota += 1.0 / (RRF_K + rank_vetorial[identificador])
            if identificador in rank_lexical:
                nota += 1.0 / (RRF_K + rank_lexical[identificador])
        base_rrf[identificador], mult = nota, 1.0
        if biblioteca and meta.get("biblioteca") == biblioteca:
            nota *= 1.15
            mult *= 1.15
        if meta.get("desatualizado") and not menciona_migracao:
            nota *= 0.5
            mult *= 0.5
        # Título da página ou seção nomeia o conceito pedido (ex.: "Operator", "Statevector").
        if termos_consulta & set(tokens(f"{meta.get('titulo', '')} {meta.get('secao', '')}")):
            bonus = float(os.getenv("RAG_BONUS_TITULO", "1.2"))
            nota *= bonus
            mult *= bonus
        # Identificadores de código citados literalmente na pergunta.
        if identificadores and any(i in textos_por_id.get(identificador, "") for i in identificadores):
            nota *= 1.25
            mult *= 1.25
        pontuacao[identificador] = nota
        multiplicador[identificador] = mult
    ordenados = sorted(pontuacao, key=lambda i: -pontuacao[i])
    if reordenar and recursos.ativo("reranker") and ordenados:
        ordenados = _aplicar_reranker(pergunta, ordenados, textos_por_id, base_rrf, multiplicador, pontuacao,
                                      topo=_topo_reranker())
    if usar_mmr and ordenados:
        ordenados = _aplicar_mmr(ordenados, pontuacao, vetores_por_id, bool(rank_vetorial))
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
