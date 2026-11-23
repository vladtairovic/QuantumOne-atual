"""Otimização multiestratégia com perfis, alvos de hardware e seleção verificada.

Cada perfil gera candidatos (transpilação em vários níveis e sementes,
laço extra de simplificação, ressíntese de Clifford) e escolhe o melhor pela
prioridade pedida. Um candidato só é aceito se a verificação de equivalência
não o refutar; candidatos refutados são descartados e registrados.
Sem alvo de hardware, o próprio circuito original é um candidato: o motor
nunca devolve um resultado pior que a entrada na métrica prioritária.
"""
from time import perf_counter

from qiskit import QuantumCircuit, transpile
from qiskit.transpiler import CouplingMap, PassManager
from qiskit.transpiler.passes import (CommutativeCancellation, ConsolidateBlocks, InverseCancellation,
                                      Optimize1qGatesDecomposition, UnitarySynthesis)
from qiskit.circuit.library import CXGate, CZGate, HGate, SwapGate, XGate, YGate, ZGate

from .analise import contar_cx_equivalentes, eh_clifford
from .errors import CircuitError

PERFIS = {
    "rapido": {"niveis": [1], "sementes": [42], "laco_extra": False, "clifford": False,
               "descricao": "Otimizações locais de baixo custo (nível 1)."},
    "balanceado": {"niveis": [2, 3], "sementes": [42], "laco_extra": True, "clifford": False,
                   "descricao": "Níveis 2 e 3 do transpilador e um laço extra de simplificação."},
    "avancado": {"niveis": [2, 3], "sementes": [42, 7, 13, 101, 2024], "laco_extra": True, "clifford": True,
                 "descricao": "Várias sementes, laço extra, ressíntese de Clifford e escolha do melhor resultado."},
    "experimental": {"niveis": [3], "sementes": list(range(1, 21)), "laco_extra": True, "clifford": True,
                     "descricao": "20 sementes e todas as estratégias; custo de compilação bem maior e ganhos "
                                  "não garantidos."},
}
PORTAS_NATIVAS = {
    "cx_u": ["cx", "u"],
    "ibm_cx": ["cx", "rz", "sx", "x"],
    "ibm_heron": ["cz", "rz", "sx", "x"],
    "ibm_eagle": ["ecr", "rz", "sx", "x"],
}
PRIORIDADES = {
    "dois_qubits": lambda m: (m["cx_equivalentes"], m["profundidade"], m["total"]),
    "profundidade": lambda m: (m["profundidade"], m["cx_equivalentes"], m["total"]),
    "total": lambda m: (m["total"], m["cx_equivalentes"], m["profundidade"]),
}


def _metricas(c):
    return {"cx_equivalentes": contar_cx_equivalentes(c), "profundidade": c.depth(), "total": c.size(),
            "dois_qubits": sum(1 for i in c.data if i.operation.num_qubits == 2 and i.operation.name != "barrier")}


def mapa_de_acoplamento(topologia: str, n: int) -> CouplingMap | None:
    if topologia in (None, "", "todos"):
        return None
    if n < 2:
        return None
    if topologia == "linear":
        return CouplingMap.from_line(n)
    if topologia == "anel":
        return CouplingMap.from_ring(n)
    if topologia == "grade":
        colunas = max(2, int(round(n ** 0.5)))
        linhas = -(-n // colunas)
        mapa = CouplingMap.from_grid(linhas, colunas)
    elif topologia == "heavy_hex":
        d = 3
        while (5 * d * d - 2 * d - 1) // 2 < n:
            d += 2
        mapa = CouplingMap.from_heavy_hex(d)
    else:
        raise CircuitError("INVALID_REQUEST", f"Topologia desconhecida: {topologia}.",
                           {"opcoes": ["linear", "anel", "grade", "heavy_hex", "todos"]})
    # Subgrafo conexo com exatamente n qubits (BFS a partir do qubit 0).
    vistos, fila = [0], [0]
    while fila and len(vistos) < n:
        atual = fila.pop(0)
        for vizinho in sorted(mapa.neighbors(atual)):
            if vizinho not in vistos and len(vistos) < n:
                vistos.append(vizinho)
                fila.append(vizinho)
    return mapa.reduce(vistos)


def _laco_extra(circuito, base):
    portas_base = base or sorted({i.operation.name for i in circuito.data} | {"cx", "u"})
    portas_1q = [g for g in portas_base if g in ("u", "u3", "rz", "sx", "x", "p", "ry", "rx")] or ["u"]
    pares = [CXGate(), CZGate(), SwapGate(), HGate(), XGate(), YGate(), ZGate()]
    pm = PassManager([
        Optimize1qGatesDecomposition(basis=portas_1q),
        CommutativeCancellation(),
        InverseCancellation(pares),
        ConsolidateBlocks(basis_gates=portas_base),
        UnitarySynthesis(basis_gates=portas_base),
        Optimize1qGatesDecomposition(basis=portas_1q),
        CommutativeCancellation(),
    ])
    anterior = None
    atual = circuito
    for _ in range(4):
        atual = pm.run(atual)
        assinatura = (atual.size(), atual.depth())
        if assinatura == anterior:
            break
        anterior = assinatura
    return atual


def _ressintese_clifford(circuito):
    from qiskit.quantum_info import Clifford
    from qiskit.synthesis import synth_clifford_greedy
    sintetizado = synth_clifford_greedy(Clifford(circuito))
    resultado = QuantumCircuit(circuito.num_qubits, circuito.num_clbits)
    resultado.compose(sintetizado, inplace=True)
    return resultado


def gerar_candidatos(original, perfil, nivel, hardware, passes):
    """Gera (nome, circuito, tempo) para o perfil escolhido."""
    config = PERFIS[perfil] if perfil else {"niveis": [nivel], "sementes": [42], "laco_extra": False, "clifford": False}
    hardware = hardware or {}
    mapa = mapa_de_acoplamento(hardware.get("topologia"), original.num_qubits)
    base = PORTAS_NATIVAS.get(hardware.get("portas_nativas")) if isinstance(hardware.get("portas_nativas"), str) \
        else hardware.get("portas_nativas")
    candidatos = []
    if mapa is None and not base:
        candidatos.append(("original", original, 0.0))
    callback = (lambda **kw: passes.add(type(kw["pass_"]).__name__)) if passes is not None else None
    for nivel_t in config["niveis"]:
        for semente in config["sementes"] if mapa is not None else config["sementes"][:1]:
            inicio = perf_counter()
            c = transpile(original, coupling_map=mapa, basis_gates=base, optimization_level=nivel_t,
                          seed_transpiler=semente, num_processes=1, callback=callback)
            candidatos.append((f"transpile_nivel{nivel_t}_semente{semente}", c, perf_counter() - inicio))
    if config["laco_extra"]:
        for nome, c, t in list(candidatos):
            if nome.startswith("transpile_nivel3") or (nome.startswith("transpile") and len(config["niveis"]) == 1):
                inicio = perf_counter()
                try:
                    extra = _laco_extra(c, base)
                    # O PassManager não propaga o layout do roteamento; sem ele a ordem
                    # lógica dos qubits seria restaurada errada.
                    extra._layout = c.layout
                    if mapa is None or _respeita_mapa(extra, mapa):
                        candidatos.append((nome + "+laco_extra", extra, t + perf_counter() - inicio))
                except Exception:
                    pass
                break
    if config["clifford"] and mapa is None and not base and eh_clifford(original) and \
            not any(i.operation.name in ("measure", "reset") for i in original.data):
        inicio = perf_counter()
        try:
            candidatos.append(("ressintese_clifford", _ressintese_clifford(original), perf_counter() - inicio))
        except Exception:
            pass
    return candidatos, mapa, base


def _respeita_mapa(circuito, mapa):
    arestas = set(mapa.get_edges())
    for item in circuito.data:
        if item.operation.num_qubits == 2 and item.operation.name != "barrier":
            a, b = (circuito.find_bit(q).index for q in item.qubits)
            if (a, b) not in arestas and (b, a) not in arestas:
                return False
    return True


def escolher(original, candidatos, prioridade, verificar_fn, logico_fn):
    chave = PRIORIDADES.get(prioridade, PRIORIDADES["dois_qubits"])
    avaliados = []
    for nome, circuito, tempo in candidatos:
        m = _metricas(circuito)
        avaliados.append({"nome": nome, "circuito": circuito, "tempo_s": round(tempo, 4), **m})
    avaliados.sort(key=lambda a: (chave(a), a["nome"] != "original"))
    escolhido, verificacao = None, None
    for candidato in avaliados:
        if candidato["nome"] == "original":
            candidato["verificacao"] = "identico_ao_original"
            escolhido, verificacao = candidato, None
            break
        resultado = verificar_fn(original, logico_fn(candidato["circuito"]))
        candidato["verificacao"] = resultado["metodo"] + ":" + (
            "equivalente" if resultado["equivalente"] else "refutado" if resultado["equivalente"] is False else "nao_verificado")
        if resultado["equivalente"] is False:
            continue
        escolhido, verificacao = candidato, resultado
        break
    if escolhido is None:
        raise CircuitError("EQUIVALENCE_FAILED", "Nenhuma proposta passou na verificação de equivalência.", status=500)
    tabela = [{k: v for k, v in a.items() if k != "circuito"} for a in avaliados]
    return escolhido, verificacao, tabela
