"""Pontuação de suporte: o quanto uma frase está sustentada por um conjunto de fontes."""
import math
from collections.abc import Callable

from .glossario import expandir
from .texto import termos_unicos, tokens


def _lexical(frase: str, fontes: list[str]) -> float:
    docs = [set(tokens(f)) for f in fontes]
    termos = termos_unicos(frase)
    if not termos:
        return 0.0
    n = len(docs)
    total = apoiado = 0.0
    for termo in termos:
        peso = math.log((n + 1) / (sum(termo in d for d in docs) + 0.5)) + 1.0
        equivalentes = {termo, *expandir([termo])}
        total += peso
        if any(equivalentes & d for d in docs):
            apoiado += peso
    return apoiado / total


def _cosseno(a: list[float], b: list[float]) -> float:
    norma = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / norma if norma else 0.0


def pontuar_suporte(frase: str, fontes: list[str],
                    embed: Callable[[list[str]], list[list[float]]] | None = None) -> float:
    """Máximo entre sobreposição lexical ponderada por IDF (com glossário PT->EN) e
    similaridade de cosseno por embedding (se ``embed``), limitado a [0, 1]."""
    if not fontes:
        return 0.0
    pontuacao = _lexical(frase, fontes)
    if embed is not None:
        vetores = embed([frase, *fontes])
        pontuacao = max(pontuacao, *(_cosseno(vetores[0], v) for v in vetores[1:]))
    return min(1.0, max(0.0, pontuacao))
