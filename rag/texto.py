"""Normalização e tokenização compartilhadas pela busca lexical, pela resposta
local e pela avaliação do RAG. Sem dependências externas."""
import re
import unicodedata

STOPWORDS = frozenset("""
a o as os um uma uns umas de do da dos das em no na nos nas por pelo pela pelos pelas
para pra com sem sob sobre entre ate até e ou mas se que qual quais quando como onde
porque por que quem cujo cuja isso isto esse essa este esta aquele aquela ele ela eles
elas eu tu voce você nos vós me te lhe seu sua seus suas meu minha meus minhas ao aos
à às é são ser estar está estão foi era há tem ter ver faz fazer pode posso devo deve
mais menos muito muita muitos muitas pouco já não nao sim também tambem só so ainda
the of and or to in on for with without by from at as is are be was were it its this that
these those an a how what which who why when where do does did can could should would
use using used into than then there their them they we you your i me my not no yes
""".split())

_PALAVRA = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*|\d+(?:\.\d+)?")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def sem_acentos(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def tokens(texto: str, *, manter_stopwords: bool = False) -> list[str]:
    """Tokens em minúsculas e sem acentos.

    Identificadores de código geram o termo completo e suas partes:
    ``QuantumCircuit.cx`` -> ``quantumcircuit.cx``, ``quantumcircuit``,
    ``quantum``, ``circuit``, ``cx``. Assim perguntas em linguagem natural e
    trechos de código compartilham vocabulário.
    """
    saida = []
    for bruto in _PALAVRA.findall(sem_acentos(texto)):
        partes = bruto.split(".")
        candidatos = [bruto] if len(partes) > 1 else []
        for parte in partes:
            candidatos.append(parte)
            sub = [s for s in _CAMEL.findall(parte) if s]
            if len(sub) > 1:
                candidatos.extend(sub)
            if "_" in parte:
                candidatos.extend(p for p in parte.split("_") if p)
        for termo in candidatos:
            termo = termo.lower()
            if len(termo) < 2 and not termo.isdigit():
                continue
            if not manter_stopwords and termo in STOPWORDS:
                continue
            saida.append(termo)
    return saida


def termos_unicos(texto: str) -> list[str]:
    return list(dict.fromkeys(tokens(texto)))


def frases(texto: str) -> list[str]:
    """Divide texto em frases, preservando blocos de código inteiros."""
    blocos = re.split(r"(```.*?```)", texto, flags=re.DOTALL)
    saida = []
    for bloco in blocos:
        if bloco.startswith("```"):
            saida.append(bloco.strip())
            continue
        for linha in bloco.split("\n\n"):
            for frase in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý0-9`])", linha.strip()):
                frase = " ".join(frase.split())
                if len(frase) >= 25:
                    saida.append(frase)
    return saida
