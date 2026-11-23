"""Verificação adaptativa de equivalência entre o circuito original e o otimizado.

Ordem de tentativa (do mais forte para o mais fraco), dentro de orçamentos:

1. ``operador``: comparação de unitárias até fase global (exata; poucos qubits).
2. ``clifford``: comparação de tableaus (exata; dezenas/centenas de qubits).
3. ``reescrita``: o circuito "miter" U·V† é simplificado pelo transpilador; se
   restar a identidade, a equivalência está provada (exata). Se não restar,
   nada se conclui.
4. ``estados_aleatorios``: os dois circuitos são aplicados a estados-produto
   aleatórios e os vetores resultantes são comparados até uma fase global
   comum (amostral: evidência forte, não prova formal). Diferenças reais mas
   desprezíveis (infidelidade <= 1e-8, fase <= 1e-4 rad) são classificadas como ``aproximada``.
5. ``amostragem``: para circuitos não unitários (medições intermediárias,
   reset, controle clássico), compara distribuições de medição por shots
   (estatística).

Medições finais são separadas e o mapeamento qubit->bit é comparado; a parte
unitária é verificada pelos métodos acima.
"""
import os
from time import perf_counter

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.exceptions import QiskitError

from .analise import eh_clifford

TOL = 1e-8


def _orcamentos():
    return {
        "operador_qubits": min(int(os.getenv("MAX_EQUIVALENCE_QUBITS", "8")), 12),
        "operador_operacoes": int(os.getenv("MAX_EQUIVALENCE_OPERATIONS", "1000")),
        "statevector_qubits": int(os.getenv("QONE_MAX_STATEVECTOR_QUBITS", "22")),
        # amplitudes x portas x estados: limita o tempo do teste amostral.
        "statevector_custo": float(os.getenv("QONE_STATEVECTOR_BUDGET", "6e8")),
        "estados_teste": int(os.getenv("QONE_ESTADOS_TESTE", "3")),
        "reescrita_operacoes": int(os.getenv("QONE_REESCRITA_OPERACOES", "20000")),
        "amostragem_qubits": int(os.getenv("QONE_AMOSTRAGEM_QUBITS", "20")),
        "shots": int(os.getenv("QONE_SHOTS_VERIFICACAO", "4000")),
    }


def _separar_medicoes_finais(circuito):
    """Retorna (parte_unitaria, mapa_medicoes) se todas as medições forem finais; senão None."""
    medidos, mapa, unitario = set(), {}, circuito.copy_empty_like()
    for item in circuito.data:
        op = item.operation
        q = [circuito.find_bit(b).index for b in item.qubits]
        if op.name == "measure":
            medidos.update(q)
            mapa[q[0]] = circuito.find_bit(item.clbits[0]).index
            continue
        if op.name == "barrier":
            continue
        if getattr(op, "blocks", ()) or op.name == "reset" or item.clbits or medidos & set(q):
            return None
        unitario.append(item)
    sem_bits = QuantumCircuit(circuito.num_qubits, global_phase=circuito.global_phase)
    for item in unitario.data:
        sem_bits.append(item.operation, [unitario.find_bit(b).index for b in item.qubits])
    return sem_bits, mapa


def _resultado(equivalente, metodo, garantia, inicio, **detalhes):
    return {"equivalente": equivalente, "metodo": metodo, "garantia": garantia,
            "tempo_s": round(perf_counter() - inicio, 4), "detalhes": detalhes}


def _por_operador(a, b):
    from qiskit.quantum_info import Operator
    return bool(Operator(a).equiv(Operator(b)))


def _por_clifford(a, b):
    from qiskit.quantum_info import Clifford
    return bool(Clifford(a) == Clifford(b))


def _por_reescrita(a, b):
    """Prova por reescrita: U·V† reduzido à identidade sem permutação residual."""
    miter = a.compose(b.inverse())
    reduzido = transpile(miter, optimization_level=3, seed_transpiler=1, num_processes=1)
    restantes = [i for i in reduzido.data if i.operation.name not in ("barrier", "id")]
    if restantes:
        return None, len(restantes)
    layout = reduzido.layout
    if layout is not None:
        final = layout.final_index_layout()
        inicial = layout.initial_index_layout()
        if final != inicial:
            return None, 0
    return True, 0


# Abaixo destes limites a diferença é ruído de ponto flutuante (equivalência).
TOL_INFIDELIDADE = 1e-9
TOL_FASE = 1e-6
# Até estes limites a diferença é real, porém desprezível (equivalência aproximada):
# o transpilador do Qiskit pode descartar rotações muito pequenas (por exemplo,
# fases controladas de uma QFT grande) ao traduzir para portas nativas.
TOL_INFIDELIDADE_APROX = float(os.getenv("QONE_TOL_INFIDELIDADE_APROX", "1e-8"))
TOL_FASE_APROX = float(os.getenv("QONE_TOL_FASE_APROX", "1e-4"))


def _estados_aleatorios(a, b, estados, semente=1234):
    """Retorna (veredito, detalhes); veredito é True, "aproximada" ou False."""
    from qiskit.quantum_info import Statevector
    rng = np.random.default_rng(semente)
    infidelidades, fases = [], []
    for k in range(estados):
        prep = QuantumCircuit(a.num_qubits)
        if k > 0:
            for q in range(a.num_qubits):
                prep.u(*rng.uniform(0, 2 * np.pi, 3), q)
        inicial = Statevector(prep)
        va, vb = inicial.evolve(a), inicial.evolve(b)
        produto = np.vdot(vb.data, va.data)
        infidelidade = max(0.0, 1 - abs(produto) ** 2)
        infidelidades.append(infidelidade)
        if infidelidade > TOL_INFIDELIDADE_APROX:
            return False, {"estado_contraexemplo": k, "infidelidade": float(infidelidade)}
        fases.append(np.angle(produto))
    # Variação da fase relativa entre estados (com desdobramento em ±pi).
    variacao = float(np.ptp(np.unwrap(np.array(fases)))) if len(fases) > 1 else 0.0
    detalhes = {"estados_testados": estados, "infidelidade_maxima": float(max(infidelidades)),
                "variacao_de_fase_rad": variacao}
    if max(infidelidades) <= TOL_INFIDELIDADE and variacao <= TOL_FASE:
        return True, detalhes
    if variacao <= TOL_FASE_APROX:
        detalhes["motivo"] = ("Diferença real, porém desprezível, entre os circuitos (provável descarte de rotações "
                              "muito pequenas pelo transpilador).")
        return "aproximada", detalhes
    detalhes["motivo"] = "A fase relativa difere entre estados de teste: não é a mesma unitária."
    return False, detalhes


def _amostragem(a, b, shots):
    from qiskit.providers.basic_provider import BasicSimulator
    try:
        from qiskit_aer import AerSimulator
        simulador, nome = AerSimulator(), "qiskit-aer"
    except ImportError:
        simulador, nome = BasicSimulator(), "BasicSimulator"
    contagens = []
    for circuito in (a, b):
        c = circuito if circuito.num_clbits else circuito.measure_all(inplace=False)
        execucao = simulador.run(transpile(c, simulador, optimization_level=0), shots=shots, seed_simulator=7)
        contagens.append(execucao.result().get_counts())
    chaves = set(contagens[0]) | set(contagens[1])
    tvd = 0.5 * sum(abs(contagens[0].get(k, 0) - contagens[1].get(k, 0)) / shots for k in chaves)
    # Tolerância conservadora para ruído de amostragem com K resultados distintos.
    tolerancia = 3 * np.sqrt(max(1, len(chaves)) / shots)
    return tvd, float(tolerancia), nome


def verificar(original: QuantumCircuit, candidato: QuantumCircuit) -> dict:
    inicio = perf_counter()
    orc = _orcamentos()
    if original.num_qubits != candidato.num_qubits:
        return _resultado(None, "nenhum", "nenhuma", inicio, motivo="Os circuitos têm números de qubits diferentes.")
    if original.parameters or candidato.parameters:
        return _resultado(None, "nenhum", "nenhuma", inicio, motivo="Parâmetros simbólicos não atribuídos.")
    n = original.num_qubits
    partes = [_separar_medicoes_finais(c) for c in (original, candidato)]
    if partes[0] is None or partes[1] is None:
        return _verificar_nao_unitario(original, candidato, orc, inicio)
    (ua, mapa_a), (ub, mapa_b) = partes
    if mapa_a != mapa_b:
        return _resultado(False, "mapa_de_medicoes", "exata", inicio,
                          motivo="As medições finais gravam qubits diferentes nos mesmos bits clássicos.")
    observacao = ("Medições finais com o mesmo mapeamento qubit->bit; a parte unitária foi verificada."
                  if mapa_a else None)
    operacoes = max(len(ua.data), len(ub.data))
    tentativas = []
    try:
        if n <= orc["operador_qubits"] and operacoes <= orc["operador_operacoes"]:
            eq = _por_operador(ua, ub)
            return _resultado(eq, "operador", "exata", inicio, observacao=observacao)
        tentativas.append(f"operador: acima de {orc['operador_qubits']} qubits ou {orc['operador_operacoes']} operações")
        if eh_clifford(ua) and eh_clifford(ub):
            eq = _por_clifford(ua, ub)
            return _resultado(eq, "clifford", "exata", inicio, observacao=observacao)
        tentativas.append("clifford: há portas fora do grupo de Clifford")
        if len(ua.data) + len(ub.data) <= orc["reescrita_operacoes"]:
            provado, restantes = _por_reescrita(ua, ub)
            if provado:
                return _resultado(True, "reescrita", "exata", inicio, observacao=observacao,
                                  explicacao="U·V† foi reduzido à identidade pelo transpilador.")
            tentativas.append(f"reescrita: {restantes} operações restaram no circuito U·V† (inconclusivo)")
        custo = (2 ** n) * operacoes * 2 * orc["estados_teste"]
        if n <= orc["statevector_qubits"] and custo <= orc["statevector_custo"]:
            eq, info = _estados_aleatorios(ua, ub, orc["estados_teste"])
            if eq == "aproximada":
                return _resultado(True, "estados_aleatorios", "aproximada", inicio, observacao=observacao,
                                  tentativas=tentativas, **info)
            # Um contraexemplo é conclusivo; a concordância em todos os estados é amostral.
            return _resultado(eq, "estados_aleatorios", "amostral" if eq else "exata", inicio,
                              observacao=observacao, tentativas=tentativas, **info)
        tentativas.append(f"estados_aleatorios: custo {custo:.1e} acima do orçamento {orc['statevector_custo']:.1e} "
                          f"ou mais de {orc['statevector_qubits']} qubits")
    except (QiskitError, MemoryError, ValueError) as erro:
        tentativas.append(f"falha: {type(erro).__name__}")
    return _resultado(None, "nenhum", "nenhuma", inicio, tentativas=tentativas,
                      motivo="Nenhum método de verificação coube no orçamento de tempo e memória.")


def _verificar_nao_unitario(original, candidato, orc, inicio):
    if original.num_qubits > orc["amostragem_qubits"]:
        return _resultado(None, "nenhum", "nenhuma", inicio,
                          motivo="Circuito com medições intermediárias, reset ou controle clássico acima do limite "
                                 "de qubits para amostragem; a equivalência unitária não se aplica.")
    try:
        tvd, tolerancia, simulador = _amostragem(original, candidato, orc["shots"])
    except Exception as erro:  # simuladores podem recusar instruções específicas
        return _resultado(None, "nenhum", "nenhuma", inicio, motivo=f"Amostragem indisponível ({type(erro).__name__}).")
    compativel = tvd <= tolerancia
    return _resultado(None, "amostragem", "estatistica", inicio, distribuicoes_compativeis=compativel,
                      distancia_variacao_total=round(tvd, 4), tolerancia=round(tolerancia, 4), shots=orc["shots"],
                      simulador=simulador,
                      motivo="Circuito não unitário (medição intermediária, reset ou controle clássico): "
                             "comparadas as distribuições de medição por amostragem; isto não prova equivalência.")
