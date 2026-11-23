"""Resposta extrativa local: funciona sem LLM, sem internet e sem cota.

Seleciona as frases mais pertinentes dos trechos recuperados e as apresenta
com a marcação [Fonte N]. Não sintetiza conhecimento novo: cada afirmação é
uma cópia literal de um trecho da base local, o que torna a resposta
verificável. Quando nenhuma frase compartilha termos com a pergunta, a função
retorna None e o chamador informa contexto insuficiente.
"""
import math
import os
from collections import Counter

from .glossario import expandir
from .texto import frases, termos_unicos, tokens

CABECALHO = ("Resposta extrativa da base local do QuantumOne (sem modelo de linguagem). "
             "Os trechos abaixo foram copiados das fontes indicadas; leia-os no contexto original.")
LIMITACAO = ("Limitação: esta resposta não interpreta nem combina as fontes; ela apenas destaca os "
             "trechos mais relacionados aos termos da pergunta.")


def _pontuar(frase_tokens, pesos):
    vistos = set(frase_tokens)
    return sum(pesos.get(t, 0.0) for t in vistos)


def gerar(pergunta: str, documentos: list[dict], max_frases: int = 6) -> dict | None:
    termos = termos_unicos(pergunta)
    termos += expandir(termos)
    if not termos or not documentos:
        return None
    candidatos = []
    df = Counter()
    # Só usa seções próximas da melhor pontuação da recuperação: trechos muito
    # abaixo do primeiro colocado tendem a ser tangenciais e geram citações fracas.
    notas = [d.get("pontuacao") for d in documentos if d.get("pontuacao") is not None]
    corte = max(notas) * float(os.getenv("QONE_LOCAL_CORTE", "0.7")) if notas else None
    for numero, documento in enumerate(documentos, start=1):
        if corte is not None and documento.get("pontuacao") is not None and documento["pontuacao"] < corte:
            continue
        for posicao, frase in enumerate(frases(documento.get("texto", ""))):
            toks = tokens(frase)
            if not toks:
                continue
            candidatos.append((numero, posicao, frase, toks))
            df.update(set(toks))
    if not candidatos:
        return None
    total = len(candidatos)
    # IDF dentro do conjunto recuperado: termos raros nas frases pesam mais.
    pesos = {t: math.log(1 + total / (1 + df[t])) for t in termos}
    pontuados = []
    for numero, posicao, frase, toks in candidatos:
        nota = _pontuar(toks, pesos)
        cobertos = len(set(toks) & set(termos))
        if cobertos == 0:
            continue
        # Penaliza frases muito longas para evitar despejar seções inteiras.
        nota /= 1 + max(0, len(toks) - 60) / 60
        pontuados.append((nota, cobertos, numero, posicao, frase))
    if not pontuados:
        return None
    pontuados.sort(key=lambda item: (-item[0], item[2], item[3]))
    escolhidos = sorted(pontuados[:max_frases], key=lambda item: (item[2], item[3]))
    linhas = [CABECALHO, ""]
    for _, _, numero, _, frase in escolhidos:
        if frase.startswith("```"):
            linhas.append(f"[Fonte {numero}]")
            linhas.append(frase)
        else:
            linhas.append(f"- {frase} [Fonte {numero}]")
    linhas += ["", LIMITACAO]
    termos_cobertos = {t for item in escolhidos for t in tokens(item[4])} & set(termos)
    return {
        "response": "\n".join(linhas),
        "fontes_usadas": sorted({item[2] for item in escolhidos}),
        "cobertura_termos": round(len(termos_cobertos) / len(set(termos)), 3),
    }
