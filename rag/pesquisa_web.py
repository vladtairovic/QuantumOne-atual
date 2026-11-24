"""Pesquisa na internet para complementar a base local (sem chave e sem cota paga).

Fontes (endereços fixos, sem seguir links arbitrários):
- Documentação oficial do catálogo verificado (``documentos/fontes_conhecimento.json``),
  baixada ao vivo pelo coletor com allowlist e robots.txt;
- Quantum Computing Stack Exchange (API pública api.stackexchange.com);
- arXiv (API pública export.arxiv.org);
- Wikipedia (API pública), só com ``QONE_WEB_CONTATO`` definido: a política da
  Wikipedia exige um contato (e-mail ou URL) no User-Agent.
A base local é sempre consultada antes; estes resultados entram no prompt como
[Web N], separados das fontes locais [Fonte N].
"""
import html
import json
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from hashlib import sha256
from pathlib import Path

import httpx

from .texto import tokens

log = logging.getLogger(__name__)
AGENTE = "QuantumOne/2.0 (pesquisa de documentacao tecnica)"


def _agente():
    contato = os.getenv("QONE_WEB_CONTATO", "").strip()
    return f"QuantumOne/2.0 ({contato})" if contato else AGENTE
CATALOGO = Path(__file__).resolve().parents[1] / "documentos" / "fontes_conhecimento.json"
_TAGS = re.compile(r"<[^>]+>")
_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="qone-web")


def ativa() -> bool:
    return os.getenv("QONE_PESQUISA_WEB", "1") == "1"


def _limpar(texto):
    return " ".join(html.unescape(_TAGS.sub(" ", texto or "")).split())


def _item(fonte, titulo, url, trecho):
    return {"fonte": fonte, "titulo": _limpar(titulo)[:200], "url": url, "trecho": _limpar(trecho)[:900]}


def palavras_chave(consulta, maximo=5):
    """Consulta curta para APIs de busca literal (Stack Exchange, arXiv): termos técnicos, sem stopwords."""
    from .texto import STOPWORDS
    vistos = []
    for palavra in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*", consulta):
        chave = palavra.lower().strip(".")
        if len(chave) > 2 and chave not in STOPWORDS and chave not in vistos:
            vistos.append(chave)
    return vistos[:maximo]


def wikipedia(cliente, consulta):
    r = cliente.get("https://en.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": consulta, "srlimit": 3, "format": "json", "utf8": 1})
    r.raise_for_status()
    itens = []
    for hit in r.json().get("query", {}).get("search", []):
        titulo = hit["title"]
        itens.append(_item("wikipedia", titulo, "https://en.wikipedia.org/wiki/" + titulo.replace(" ", "_"), hit.get("snippet", "")))
    return itens


def arxiv(cliente, consulta):
    termos = " AND ".join(f"all:{t}" for t in palavras_chave(consulta, 3))
    r = cliente.get("https://export.arxiv.org/api/query", params={"search_query": termos or consulta, "max_results": 3,
                                                                  "sortBy": "relevance"})
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    itens = []
    for entrada in ET.fromstring(r.text).findall("a:entry", ns):
        titulo = entrada.findtext("a:title", "", ns)
        url = entrada.findtext("a:id", "", ns)
        resumo = entrada.findtext("a:summary", "", ns)
        if url.startswith("http://arxiv.org/"):
            url = "https://" + url[len("http://"):]
        if url.startswith("https://arxiv.org/abs/"):
            itens.append(_item("arxiv", titulo, url, resumo))
    return itens


def stackexchange(cliente, consulta):
    """Busca literal (todos os termos): tenta 3 palavras-chave e, sem resultado, 2.

    Sem chave a API permite ~300 consultas/dia por IP; ``STACKEXCHANGE_KEY`` (gratuita) amplia o limite.
    """
    termos = palavras_chave(consulta, 3)
    dados = {"items": []}
    for quantidade in (3, 2):
        if len(termos) < quantidade and quantidade != 2:
            continue
        parametros = {"order": "desc", "sort": "relevance", "q": " ".join(termos[:quantidade]),
                      "site": "quantumcomputing", "pagesize": 4}
        if os.getenv("STACKEXCHANGE_KEY", "").strip():
            parametros["key"] = os.getenv("STACKEXCHANGE_KEY").strip()
        r = cliente.get("https://api.stackexchange.com/2.3/search/excerpts", params=parametros)
        r.raise_for_status()
        dados = r.json()
        if dados.get("items"):
            break
    itens = []
    for hit in dados.get("items", []):
        tipo = hit.get("item_type")
        ident = hit.get("question_id") if tipo == "question" else hit.get("answer_id")
        if not ident:
            continue
        caminho = "q" if tipo == "question" else "a"
        itens.append(_item("stackexchange", hit.get("title", ""), f"https://quantumcomputing.stackexchange.com/{caminho}/{ident}",
                           hit.get("excerpt", "") or hit.get("body", "")))
    return itens


def _catalogo():
    try:
        return json.loads(CATALOGO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# Hosts já cobertos por uma API própria (arXiv) não passam pelo coletor de páginas.
_HOSTS_COM_API = {"arxiv.org", "export.arxiv.org"}
# Pesquisa ao vivo não espera Crawl-delay longo: se o robots.txt pedir mais que isto, a página é pulada.
ATRASO_MAXIMO = float(os.getenv("QONE_WEB_ATRASO_MAXIMO", "3"))


def _coletar_pagina(fonte, termos):
    """Baixa uma página do catálogo com o coletor (allowlist + robots.txt) e extrai os trechos relevantes."""
    from urllib.parse import urlsplit

    from .coleta_web import AGENTE, Coletor
    with Coletor() as coletor:
        regras = coletor._robots(urlsplit(fonte["url"]).hostname)
        atraso = regras.crawl_delay(AGENTE) or 1
        if atraso > ATRASO_MAXIMO:
            raise TimeoutError(f"robots.txt pede {atraso}s entre requisições")
        titulo, secoes = coletor.coletar(fonte["url"])
        url_final = getattr(coletor, "ultima_url", fonte["url"])
    blocos = [(s["titulo"], b["texto"]) for s in secoes for b in s["blocos"]]
    melhores = sorted(blocos, key=lambda b: -len(termos & set(tokens(b[0] + " " + b[1]))))[:3]
    trecho = " ... ".join(f"{t}: {x[:300]}" for t, x in melhores)
    return _item("documentacao_oficial", titulo, url_final, trecho)


def documentacao_oficial(consultas, maximo_paginas=2):
    """Escolhe páginas do catálogo verificado pelo título/temas e baixa-as em paralelo."""
    from urllib.parse import urlsplit
    catalogo = [f for f in _catalogo() if urlsplit(f["url"]).hostname not in _HOSTS_COM_API]
    termos = set(t for c in consultas for t in tokens(c))
    if not catalogo or not termos:
        return []
    pontos = []
    for fonte in catalogo:
        descricao = " ".join([fonte["document_id"], fonte["url"].rsplit("/", 1)[-1].replace("-", " "),
                              " ".join(fonte.get("temas") or []), fonte.get("versao", "")])
        comuns = termos & set(tokens(descricao))
        if comuns:
            pontos.append((len(comuns), fonte))
    pontos.sort(key=lambda p: -p[0])
    escolhidas = [fonte for _, fonte in pontos[:maximo_paginas]]
    with ThreadPoolExecutor(max_workers=max(1, len(escolhidas))) as executor:
        futuros = {executor.submit(_coletar_pagina, fonte, termos): fonte["url"] for fonte in escolhidas}
    itens = []
    for futuro, url in futuros.items():
        try:
            itens.append(futuro.result())
        except Exception as erro:  # página indisponível, bloqueada por robots ou lenta demais
            log.info("Documentação %s indisponível: %s", url, erro)
    return itens


def _ordenar(itens, consultas):
    termos = [t for c in consultas for t in tokens(c)]
    df = Counter(t for i in itens for t in set(tokens(i["titulo"] + " " + i["trecho"])))
    def nota(item):
        toks = set(tokens(item["titulo"] + " " + item["trecho"]))
        return sum(math.log(1 + len(itens) / (1 + df[t])) for t in set(termos) if t in toks)
    return sorted(itens, key=nota, reverse=True)


def pesquisar(consultas, maximo=6, prazo=12.0):
    """Executa as fontes em paralelo (cada uma com prazo) e devolve resultados e relatório."""
    consultas = [c.strip() for c in consultas if c and c.strip()][:3]
    relatorio = {"consultas": consultas, "fontes": {}, "falhas": {}}
    if not consultas:
        return [], relatorio
    inicio = time.monotonic()
    cliente = httpx.Client(timeout=httpx.Timeout(prazo, connect=5.0), headers={"User-Agent": _agente()}, follow_redirects=False)
    tarefas = {}
    try:
        principal = consultas[0]
        tarefas[_EXECUTOR.submit(stackexchange, cliente, principal)] = "stackexchange"
        tarefas[_EXECUTOR.submit(arxiv, cliente, principal)] = "arxiv"
        if os.getenv("QONE_WEB_CONTATO", "").strip():
            tarefas[_EXECUTOR.submit(wikipedia, cliente, principal)] = "wikipedia"
        tarefas[_EXECUTOR.submit(documentacao_oficial, consultas)] = "documentacao_oficial"
        feitas, pendentes = wait(tarefas, timeout=prazo + 4)
        itens = []
        for futuro in feitas:
            nome = tarefas[futuro]
            try:
                resultado = futuro.result()
                relatorio["fontes"][nome] = len(resultado)
                itens.extend(resultado)
            except Exception as erro:
                relatorio["falhas"][nome] = type(erro).__name__
        for futuro in pendentes:
            relatorio["falhas"][tarefas[futuro]] = "tempo_esgotado"
    finally:
        cliente.close()
    vistos, unicos = set(), []
    for item in _ordenar(itens, consultas):
        if item["url"] not in vistos and item["trecho"]:
            vistos.add(item["url"])
            unicos.append(item)
    relatorio["segundos"] = round(time.monotonic() - inicio, 2)
    return unicos[:maximo], relatorio


def como_fontes(itens):
    agora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return [{"document_id": "web_" + sha256(i["url"].encode()).hexdigest()[:20],
             "chunk_id": "web_" + sha256((i["url"] + i["trecho"]).encode()).hexdigest()[:20],
             "source": i["url"], "content": i["trecho"], "origin": "web",
             "title": f"{i['titulo']} ({i['fonte']})", "retrieved_at": agora} for i in itens]


def como_contexto(itens, limite=4000):
    partes, tamanho = [], 0
    for n, i in enumerate(itens, start=1):
        bloco = f"[Web {n}]\nTítulo: {i['titulo']}\nOrigem: {i['fonte']}\nURL: {i['url']}\nTrecho: {i['trecho']}"
        if tamanho + len(bloco) > limite:
            break
        partes.append(bloco)
        tamanho += len(bloco) + 2
    return "\n\n".join(partes)
