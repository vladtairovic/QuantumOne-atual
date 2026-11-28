"""Verificação frase a frase: cada frase da resposta precisa de suporte nas fontes citadas.

Frases sem ``[Fonte N]`` são pontuadas contra todas as fontes; citações fora do intervalo
tornam a frase não suportada. Blocos de código e o cabeçalho/limitação da resposta local
não são avaliados.
"""
import math
import os
import re

from .resposta_local import CABECALHO, LIMITACAO
from .suporte import pontuar_suporte
from .texto import frases

_CITACAO = re.compile(r"\[Fonte (\d+)\]")
_CITACOES_APOS_PONTO = re.compile(r"([.!?])[ \t]*((?:\[Fonte \d+\][ \t]*)+)")


# Padrão 0.5. O limiar de maior F1 da calibração (``--calibrar-limiar``) foi 0.70 (dev F1 0.9988,
# reservado 0.9978), mas essa calibração compara frases literais da resposta extrativa local com a
# própria fonte, contra negativos de outro tema; ela NÃO se transfere a respostas livres/parafraseadas
# da LLM (uma paráfrase PT fiel de uma fonte EN pontuou 0.56 com ``embed=None``). O limiar e
# RAG_MIN_FIDELIDADE devem ser recalibrados com ``--respostas`` (respostas de LLM) e/ou com o embedder
# passado em ``embed`` antes de a camada ser ligada por padrão.
def _limiar_padrao(padrao=0.5):
    try:
        valor = float(os.getenv("RAG_LIMIAR_SUPORTE", str(padrao)))
    except ValueError:
        return padrao
    return min(1.0, max(0.0, valor)) if math.isfinite(valor) else padrao


def _frases_avaliaveis(resposta: str) -> list[str]:
    texto = resposta.replace(CABECALHO, "").replace(LIMITACAO, "")
    texto = _CITACOES_APOS_PONTO.sub(lambda m: m.group(2).strip() + m.group(1), texto)
    saida = []
    for parte in re.split(r"(```.*?```)", texto, flags=re.DOTALL):
        if parte.startswith("```"):
            continue
        for linha in parte.split("\n"):
            linha = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", linha)
            saida.extend(frases(linha))
    return saida


def verificar_resposta(resposta: str, fontes: list[dict], embed=None, limiar: float | None = None) -> dict:
    limiar = _limiar_padrao() if limiar is None else limiar
    resposta = resposta or ""
    if not resposta.strip():
        return {"frases": [], "fidelidade": 0.0, "texto": resposta}
    avaliadas = []
    for frase in _frases_avaliaveis(resposta):
        citadas = list(dict.fromkeys(int(n) for n in _CITACAO.findall(frase)))
        limpa = " ".join(_CITACAO.sub("", frase).split())
        if not limpa:
            continue
        if citadas:
            validas = [n for n in citadas if 1 <= n <= len(fontes)]
            alvo = [fontes[n - 1].get("content", "") for n in validas]
            suporte = pontuar_suporte(limpa, alvo, embed) if validas else 0.0
        else:
            validas = []
            suporte = pontuar_suporte(limpa, [f.get("content", "") for f in fontes], embed)
        avaliadas.append({"texto": limpa, "fontes": citadas, "suporte": suporte,
                          "suportada": (not citadas or bool(validas)) and suporte >= limiar})
    fidelidade = sum(f["suportada"] for f in avaliadas) / len(avaliadas) if avaliadas else 1.0
    return {"frases": avaliadas, "fidelidade": fidelidade, "texto": resposta}
