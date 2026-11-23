"""Análise estrutural de circuitos e estimativa de recursos de simulação."""
from collections import Counter

from qiskit import QuantumCircuit

AUTOINVERSAS = {"h", "x", "y", "z", "cx", "cy", "cz", "swap", "ccx", "id"}
PARES_INVERSOS = {("s", "sdg"), ("sdg", "s"), ("t", "tdg"), ("tdg", "t"), ("sx", "sxdg"), ("sxdg", "sx")}
ROTACOES = {"rx", "ry", "rz", "p", "u1", "crx", "cry", "crz", "cp", "rxx", "ryy", "rzz"}
DIAGONAIS = {"z", "s", "sdg", "t", "tdg", "rz", "p", "u1"}
CLIFFORD = {"h", "s", "sdg", "x", "y", "z", "cx", "cy", "cz", "swap", "id", "sx", "sxdg", "measure", "reset",
            "barrier", "ecr", "iswap", "dcx"}
BYTES_AMPLITUDE = 16


def _eh_diretiva(op):
    return getattr(op, "_directive", False) or op.name == "barrier"


def contar_cx_equivalentes(circuito: QuantumCircuit) -> int:
    """Contagem de CX após decompor o circuito para {cx, u} sem otimizar.

    Mede o custo real de portas de dois qubits (um SWAP vale 3 CX, uma CCX vale 6).
    """
    from qiskit import transpile
    if not any(i.operation.num_qubits >= 2 and not _eh_diretiva(i.operation) for i in circuito.data):
        return 0
    try:
        decomposto = transpile(circuito, basis_gates=["cx", "u", "measure", "reset"],
                               optimization_level=0, num_processes=1)
    except Exception:
        return sum(1 for i in circuito.data if i.operation.num_qubits == 2 and not _eh_diretiva(i.operation))
    return int(decomposto.count_ops().get("cx", 0))


def eh_clifford(circuito: QuantumCircuit) -> bool:
    nomes = {i.operation.name for i in circuito.data}
    if not nomes <= CLIFFORD or any(getattr(i.operation, "blocks", ()) for i in circuito.data):
        return False
    return not any(getattr(i.operation, "params", None) for i in circuito.data)


def _redundancias(circuito):
    """Pares adjacentes (por qubit) que se cancelam ou se fundem."""
    ultimo = {}
    inversos, fundiveis, diag_antes_medicao = 0, 0, 0
    for item in circuito.data:
        op = item.operation
        if _eh_diretiva(op):
            for q in item.qubits:
                ultimo.pop(q, None)
            continue
        chave = tuple(item.qubits)
        anteriores = [ultimo.get(q) for q in item.qubits]
        anterior = anteriores[0] if anteriores else None
        if anterior is not None and all(a is anterior for a in anteriores):
            if tuple(anterior.qubits) == chave:
                a, b = anterior.operation.name, op.name
                if (a == b and a in AUTOINVERSAS) or (a, b) in PARES_INVERSOS:
                    inversos += 1
                elif a == b and a in ROTACOES:
                    fundiveis += 1
                if op.name == "measure" and a in DIAGONAIS:
                    diag_antes_medicao += 1
        for q in item.qubits:
            ultimo[q] = item
    return {"pares_inversos_adjacentes": inversos, "rotacoes_fundiveis_adjacentes": fundiveis,
            "portas_diagonais_antes_de_medicao": diag_antes_medicao}


def analisar_estrutura(circuito: QuantumCircuit) -> dict:
    n = circuito.num_qubits
    contagem = Counter(i.operation.name for i in circuito.data)
    por_qubit = Counter()
    dois_qubits_por_qubit = Counter()
    pares = Counter()
    medicoes_intermediarias = False
    medidos = set()
    tem_controle = False
    for item in circuito.data:
        op = item.operation
        indices = [circuito.find_bit(q).index for q in item.qubits]
        if getattr(op, "blocks", ()):
            tem_controle = True
        if op.name == "measure":
            medidos.update(indices)
        elif not _eh_diretiva(op) and medidos & set(indices):
            medicoes_intermediarias = True
        for q in indices:
            por_qubit[q] += 1
        if op.num_qubits == 2 and not _eh_diretiva(op):
            for q in indices:
                dois_qubits_por_qubit[q] += 1
            pares[tuple(sorted(indices))] += 1
    graus = Counter()
    for a, b in pares:
        graus[a] += 1
        graus[b] += 1
    cadeia = all(abs(a - b) == 1 for a, b in pares)
    profundidade_2q = circuito.depth(lambda i: i.operation.num_qubits >= 2 and not _eh_diretiva(i.operation))
    gargalo = dois_qubits_por_qubit.most_common(1)
    return {
        "qubits": n, "bits_classicos": circuito.num_clbits,
        "profundidade": circuito.depth(), "profundidade_dois_qubits": profundidade_2q,
        "total_operacoes": circuito.size(), "tipos_operacoes": dict(contagem),
        "operacoes_dois_qubits": sum(dois_qubits_por_qubit.values()) // 2,
        "operacoes_multiqubit_3mais": sum(1 for i in circuito.data if i.operation.num_qubits >= 3 and not _eh_diretiva(i.operation)),
        "cx_equivalentes": contar_cx_equivalentes(circuito),
        "medicoes": contagem.get("measure", 0), "medicoes_intermediarias": medicoes_intermediarias,
        "resets": contagem.get("reset", 0), "barreiras": contagem.get("barrier", 0),
        "controle_classico": tem_controle, "parametros_simbolicos": len(circuito.parameters),
        "qubits_ociosos": [q for q in range(n) if por_qubit[q] == 0],
        "pares_interagindo": len(pares), "grau_maximo_interacao": max(graus.values(), default=0),
        "interacoes_apenas_vizinhos": cadeia,
        "gargalo": {"qubit": gargalo[0][0], "portas_dois_qubits": gargalo[0][1]} if gargalo else None,
        "clifford": eh_clifford(circuito),
        "redundancias": _redundancias(circuito),
    }


def escolher_metodo_simulacao(analise: dict, memoria_mb: int) -> dict:
    """Método de simulação adequado ao circuito e à memória disponível."""
    n = analise["qubits"]
    bytes_sv = BYTES_AMPLITUDE * 2 ** n
    # Vetor de estado mais cópias temporárias: exige folga de 3x.
    cabe_sv = 3 * bytes_sv <= memoria_mb * 2 ** 20
    alternativas = []
    if analise["clifford"]:
        escolha = {"metodo": "estabilizador", "exato": True,
                   "justificativa": "Circuito de Clifford: simulação estabilizadora em tempo polinomial (Gottesman-Knill)."}
    elif cabe_sv:
        escolha = {"metodo": "statevector", "exato": True,
                   "justificativa": f"O vetor de estado ocupa {bytes_sv / 2 ** 20:.1f} MiB e cabe no orçamento de {memoria_mb} MiB."}
    elif analise["interacoes_apenas_vizinhos"] or analise["profundidade_dois_qubits"] <= 2 * max(1, n.bit_length()):
        escolha = {"metodo": "matrix_product_state", "exato": True,
                   "justificativa": "Interações locais ou poucas camadas de emaranhamento: MPS tende a manter a dimensão "
                                    "de ligação pequena (exato sem truncamento; requer qiskit-aer)."}
    else:
        escolha = {"metodo": "nenhum_exato_viavel", "exato": False,
                   "justificativa": f"Statevector exigiria {bytes_sv / 2 ** 30:.2f} GiB e o circuito tem emaranhamento "
                                    "extenso; não há método exato viável neste orçamento."}
        alternativas = ["MPS com truncamento (aproximado)", "reduzir o circuito ou o número de qubits",
                        "executar em hardware real", "amostragem com simulador em máquina com mais memória"]
    if escolha["metodo"] != "statevector":
        alternativas.append(f"statevector: {bytes_sv / 2 ** 20:.0f} MiB {'(cabe)' if cabe_sv else '(não cabe)'}")
    return {**escolha, "memoria_statevector_bytes": bytes_sv, "memoria_orcamento_mb": memoria_mb,
            "alternativas": alternativas}
