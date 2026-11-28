import math

from . import recursos

MENSAGEM_SEM_CONTEXTO = (
    "Não encontrei documentação suficiente "
    "para responder à pergunta com segurança."
)

_MINIMO_TEXTO = 100
_PISO_PRIMEIRO = 0.4


def _cabecalho(documento, numero):
    metadados = documento.get("metadados") or {}
    return (
        f"[Fonte {numero}]\n"
        f"Título: {metadados.get('titulo', 'Sem título')}\n"
        f"Seção: {metadados.get('secao', 'Não informada')}\n"
        f"URL: {metadados.get('fonte', '')}\n"
        "Conteúdo:\n"
    )


def _fonte(documento, numero):
    metadados = documento.get("metadados") or {}
    return {
        "numero": numero,
        "titulo": metadados.get("titulo", "Sem título"),
        "secao": metadados.get("secao", "Não informada"),
        "url": metadados.get("fonte", ""),
        "id_documento": metadados.get("document_id", ""),
    }


def _sequencial(documentos, limite):
    """Preenche o limite na ordem dos documentos. Devolve (partes, fontes, completo),
    onde ``completo`` indica que todos entraram sem corte."""
    partes, fontes, tamanho = [], [], 0
    for documento in documentos:
        texto = documento["texto"].strip()
        numero = len(fontes) + 1
        cabecalho = _cabecalho(documento, numero)
        restante = limite - tamanho - len(cabecalho) - 2
        if restante < _MINIMO_TEXTO:
            return partes, fontes, False
        trecho = texto[:restante]
        bloco = cabecalho + trecho
        partes.append(bloco)
        tamanho += len(bloco) + 2
        fontes.append(_fonte(documento, numero))
        if len(trecho) < len(texto):
            return partes, fontes, False
    return partes, fontes, True


def _pesos(documentos):
    """Pesos pela ``pontuacao``; ausente/inválida vale a média das válidas (ou 1)."""
    brutos = []
    for documento in documentos:
        nota = documento.get("pontuacao")
        valida = isinstance(nota, (int, float)) and not isinstance(nota, bool) and math.isfinite(nota) and nota >= 0
        brutos.append(float(nota) if valida else None)
    presentes = [p for p in brutos if p is not None]
    media = sum(presentes) / len(presentes) if presentes else 1.0
    pesos = [media if p is None else p for p in brutos]
    return pesos if sum(pesos) > 0 else [1.0] * len(pesos)


def _fatias(pesos, necessidade, limite):
    """Reparte ``limite`` (cabeçalho + texto) entre os documentos: proporcional ao peso, primeiro com
    ao menos 40%, e a sobra de quem precisa de menos que sua fatia vai para os demais."""
    n = len(pesos)
    fatias = [0.0] * n
    abertos = list(range(n))
    restante = float(limite)
    def reparte(indices, quantia):
        # Peso total zero entre os abertos: divisão igual (evita divisão por zero).
        total = sum(pesos[i] for i in indices)
        return {i: quantia * (pesos[i] / total if total > 0 else 1 / len(indices)) for i in indices}

    while abertos:
        parcial = reparte(abertos, restante)
        if 0 in abertos and parcial[0] < _PISO_PRIMEIRO * limite:
            outros = [i for i in abertos if i != 0]
            parcial[0] = min(_PISO_PRIMEIRO * limite, restante)
            if outros:
                parcial.update(reparte(outros, restante - parcial[0]))
        satisfeitos = [i for i in abertos if necessidade[i] <= parcial[i]]
        if not satisfeitos:
            for i in abertos:
                fatias[i] = parcial[i]
            break
        for i in satisfeitos:
            fatias[i] = necessidade[i]
            restante -= necessidade[i]
            abertos.remove(i)
    return fatias


def _proporcional(documentos, limite):
    candidatos = list(documentos)
    while True:
        cabecalhos = [_cabecalho(d, n) for n, d in enumerate(candidatos, start=1)]
        textos = [d["texto"].strip() for d in candidatos]
        necessidade = [len(c) + len(t) + 2 for c, t in zip(cabecalhos, textos)]
        fatias = _fatias(_pesos(candidatos), necessidade, limite)
        uteis = [f - len(c) - 2 for f, c in zip(fatias, cabecalhos)]
        if len(candidatos) == 1 or all(u >= _MINIMO_TEXTO for u in uteis):
            break
        candidatos.pop()  # descarta o de menor prioridade (o último) e refaz a divisão
    partes, fontes = [], []
    for numero, (documento, cabecalho, texto, util) in enumerate(zip(candidatos, cabecalhos, textos, uteis), 1):
        partes.append(cabecalho + texto[:max(0, int(util))])
        fontes.append(_fonte(documento, numero))
    return partes, fontes


def preparar_contexto(documentos, limite=12000):
    """
    Constrói o contexto para a LLM.

    Preserva os títulos, seções e URLs das fontes.
    O limite é medido em caracteres, não tokens.
    Com ``RAG_ORCAMENTO=1``, quando é preciso cortar e há mais de um documento, o orçamento
    é dividido proporcionalmente à ``pontuacao`` (o primeiro recebe pelo menos 40%).
    Desligado (padrão), o preenchimento é sequencial.
    """

    if limite < 500:
        raise ValueError(
            "O limite de contexto deve ser de pelo menos 500."
        )

    com_texto = [d for d in documentos if d.get("texto", "").strip()]
    partes, fontes, completo = _sequencial(com_texto, limite)
    if recursos.ativo("orcamento") and len(com_texto) > 1 and not completo:
        partes, fontes = _proporcional(com_texto, limite)
    return "\n\n".join(partes), fontes
