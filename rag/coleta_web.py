"""Coleta administrativa de documentação, restrita a uma allowlist de domínios.

Não é usada durante perguntas: só roda pela sincronização manual/em lote
(``python -m rag.sincronizar_conhecimento``). Não usa API de busca; baixa as
páginas listadas no catálogo, respeitando robots.txt e crawl-delay de cada
site, sem seguir redirecionamentos para fora da allowlist.
"""
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

AGENTE = "QuantumOneKnowledge/2.0 (+coleta administrativa de documentacao)"
MAX_BYTES = 8 * 1024 * 1024

# host -> prefixos de caminho permitidos. Somente documentação técnica pública.
PERMITIDOS = {
    "quantum.cloud.ibm.com": ("/docs/en/", "/learning/en/"),
    "qiskit.github.io": ("/qiskit-aer/", "/qiskit-ibm-runtime/", "/qiskit-addon-", "/qiskit-experiments/"),
    "qiskit-community.github.io": ("/qiskit-optimization/", "/qiskit-machine-learning/", "/qiskit-algorithms/",
                                   "/qiskit-nature/", "/qiskit-finance/", "/qiskit-dynamics/"),
    "openqasm.com": ("/",),
    "docs.pennylane.ai": ("/en/stable/",),
    "pennylane.ai": ("/qml/",),
    "quantumai.google": ("/cirq/",),
    "learn.microsoft.com": ("/en-us/azure/quantum/",),
    "docs.aws.amazon.com": ("/braket/",),
    "qutip.readthedocs.io": ("/en/",),
    "qutip-qip.readthedocs.io": ("/en/",),
    "quantumalgorithmzoo.org": ("/",),
    "docs.python.org": ("/3/",),
    "numpy.org": ("/doc/",),
    "docs.scipy.org": ("/doc/",),
    "arxiv.org": ("/abs/",),
}


def validar_url(url):
    p = urlsplit(url)
    prefixos = PERMITIDOS.get(p.hostname or "")
    if (p.scheme != "https" or prefixos is None or p.port not in (None, 443)
            or p.username or p.password or p.query or p.fragment
            or not (p.path.startswith(prefixos) or p.path == "/robots.txt")):
        raise ValueError("Fonte fora da allowlist de documentação.")
    return url


def baixar(cliente, url, com_url=False):
    validar_url(url)
    for _ in range(4):
        texto, destino = _baixar_com_tentativas(cliente, url)
        if texto is not None:
            return (texto, url) if com_url else texto
        url = validar_url(destino)
    raise ValueError("Redirecionamentos excessivos.")


def _baixar_com_tentativas(cliente, url):
    """Repete falhas transitórias até três vezes, com espera exponencial."""
    for tentativa in range(3):
        try:
            return _baixar(cliente, url)
        except httpx.HTTPStatusError as erro:
            if erro.response.status_code not in (429, 500, 502, 503, 504) or tentativa == 2:
                raise
        except httpx.TransportError:
            if tentativa == 2:
                raise
        time.sleep(2 ** (tentativa + 1))


def _baixar(cliente, url):
    with cliente.stream("GET", url) as r:
        if r.is_redirect:
            return None, urljoin(url, r.headers["location"])
        r.raise_for_status()
        if not any(t in r.headers.get("content-type", "") for t in ("text/html", "text/plain")):
            raise ValueError("Formato de página não aceito.")
        dados = bytearray()
        for parte in r.iter_bytes():
            dados.extend(parte)
            if len(dados) > MAX_BYTES:
                raise ValueError("Página acima de 8 MiB.")
        return dados.decode(r.encoding or "utf-8", errors="replace"), url


def _conteudo_principal(soup):
    for seletor in ("main", "article", "[role=main]", "div.document", "div.body", "#content", "body"):
        no = soup.select_one(seletor)
        if no is not None and len(no.get_text(" ", strip=True)) >= 200:
            return no
    return None


def extrair(html):
    soup = BeautifulSoup(html, "html.parser")
    main = _conteudo_principal(soup)
    if main is None:
        raise ValueError("Página sem conteúdo principal.")
    for node in main.select("script,style,nav,footer,header,button,svg,form,aside,noscript,"
                            "[role=navigation],.headerlink,.sidebar,.toctree-wrapper"):
        node.decompose()
    titulo = main.find("h1") or soup.find("h1") or soup.find("title")
    titulo = titulo.get_text(" ", strip=True) if titulo else "Documentação"
    secoes, atual = [], None
    for node in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "table", "blockquote"]):
        if node.find_parent(["pre", "li", "table", "blockquote"]):
            continue
        codigo = node.name == "pre"
        texto = node.get_text("" if codigo else "\n" if node.name == "table" else " ", strip=not codigo)
        if not texto or not texto.strip():
            continue
        if node.name in ("h1", "h2", "h3", "h4"):
            atual = {"titulo": f"{len(secoes) + 1}. {texto[:160]}", "blocos": []}
            secoes.append(atual)
        else:
            if atual is None:
                atual = {"titulo": "Introdução", "blocos": []}
                secoes.append(atual)
            # Código é preservado como trecho documental (nunca executado).
            atual["blocos"].append({"tipo": "codigo" if codigo else "paragrafo", "texto": texto})
    secoes = [s for s in secoes if s["blocos"]]
    tamanho = sum(len(b["texto"]) for s in secoes for b in s["blocos"])
    if tamanho < 200 or tamanho > 800000:
        raise ValueError("Conteúdo vazio, bloqueado ou acima do limite.")
    return titulo, secoes


class Coletor:
    def __enter__(self):
        self.cliente = httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": AGENTE})
        self.robots = {}
        return self

    def _robots(self, host):
        if host not in self.robots:
            regras = RobotFileParser()
            try:
                regras.parse(baixar(self.cliente, f"https://{host}/robots.txt").splitlines())
            except httpx.HTTPStatusError as erro:
                # 404 em robots.txt significa ausência de restrições (RFC 9309).
                if erro.response.status_code != 404:
                    raise
                regras.parse([])
            self.robots[host] = regras
        return self.robots[host]

    def coletar(self, url):
        validar_url(url)
        regras = self._robots(urlsplit(url).hostname)
        if not regras.can_fetch(AGENTE, url):
            raise ValueError("Coleta não permitida por robots.txt.")
        time.sleep(max(1, regras.crawl_delay(AGENTE) or 1))
        html, self.ultima_url = baixar(self.cliente, url, com_url=True)
        return extrair(html)

    def __exit__(self, *args):
        self.cliente.close()
