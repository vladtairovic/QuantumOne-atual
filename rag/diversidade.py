"""Diversidade na seleção de trechos: Maximal Marginal Relevance (MMR)."""
import numpy as np


def mmr(itens: list[tuple[str, list[float], float]], lambda_: float = 0.7, k: int = 5) -> list[str]:
    """Escolhe até ``k`` ids equilibrando nota e diversidade.

    ``itens`` são ``(id, vetor, nota)``. O primeiro escolhido é o de maior nota; os
    seguintes maximizam ``lambda_ * nota_norm - (1 - lambda_) * max_cos(selecionados)``,
    com a nota normalizada por min-max sobre a entrada (notas todas iguais valem 0.0).
    Vetor de norma zero tem similaridade 0 com tudo. Empates seguem a ordem da entrada.
    ``lambda_=1.0`` equivale à ordenação (estável) por nota. Vetores de dimensões
    diferentes levantam ``ValueError``."""
    if k <= 0 or not itens:
        return []
    if len({len(vetor) for _, vetor, _ in itens}) > 1:
        raise ValueError("Os vetores devem ter a mesma dimensão.")
    matriz = np.asarray([vetor for _, vetor, _ in itens], dtype=np.float64)
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    matriz = np.divide(matriz, normas, out=np.zeros_like(matriz), where=normas > 0)
    similaridade = matriz @ matriz.T
    notas = np.asarray([nota for _, _, nota in itens], dtype=np.float64)
    amplitude = notas.max() - notas.min()
    normalizadas = (notas - notas.min()) / amplitude if amplitude > 0 else np.zeros_like(notas)

    escolhidos = [int(np.argmax(notas))]  # argmax devolve o primeiro em caso de empate
    restantes = [i for i in range(len(itens)) if i != escolhidos[0]]
    while restantes and len(escolhidos) < k:
        melhor, melhor_valor = None, None
        for i in restantes:
            valor = lambda_ * normalizadas[i] - (1 - lambda_) * similaridade[i, escolhidos].max()
            if melhor_valor is None or valor > melhor_valor:
                melhor, melhor_valor = i, valor
        escolhidos.append(melhor)
        restantes.remove(melhor)
    return [itens[i][0] for i in escolhidos]
