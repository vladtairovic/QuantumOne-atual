"""Prioridade 4: perfis de otimização, verificação adaptativa e suporte até 30 qubits."""
import pytest
from fastapi.testclient import TestClient
from qiskit import QuantumCircuit, qasm2
from qiskit.circuit.random import random_circuit
from qiskit.quantum_info import Operator

from main import app
from quantum import verificacao
from quantum.analise import analisar_estrutura, escolher_metodo_simulacao
from quantum.otimizador import mapa_de_acoplamento
from quantum.qiskit_service import processar_circuito


def ghz(n, redundante=False, medir=False):
    qc = QuantumCircuit(n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
        if redundante:
            qc.cx(i, i + 1)
            qc.cx(i, i + 1)
    if medir:
        qc.measure_all()
    return qc


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("perfil", ["rapido", "balanceado", "avancado", "experimental"])
def test_perfis_reduzem_redundancias_com_verificacao(perfil):
    qc = QuantumCircuit(3)
    qc.h(0); qc.h(0); qc.cx(0, 1); qc.cx(0, 1); qc.ccx(0, 1, 2); qc.rz(0.3, 2); qc.rz(0.4, 2)
    r = processar_circuito(qasm2.dumps(qc), "qasm", 3, perfil=perfil)
    assert r["perfil"] == perfil and r["equivalente"] is True
    assert r["verificacao"]["garantia"] == "exata"
    assert r["analise_final"]["cx_equivalentes"] < r["analise_original"]["cx_equivalentes"]
    assert len(r["estrategias_avaliadas"]) >= 2
    saida = qasm2.loads(r["circuito_otimizado_qasm"], custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert Operator(saida).equiv(Operator(qc))


def test_nunca_piora_a_metrica_prioritaria_sem_hardware():
    rc = random_circuit(10, 8, max_operands=2, seed=1010)
    r = processar_circuito(qasm2.dumps(rc), "qasm", 3, perfil="avancado")
    assert r["analise_final"]["cx_equivalentes"] <= r["analise_original"]["cx_equivalentes"]
    assert r["equivalente"] is True


@pytest.mark.parametrize("n", [16, 20, 25, 30])
def test_clifford_ate_30_qubits_verificado_exatamente(n):
    r = processar_circuito(qasm2.dumps(ghz(n, redundante=True, medir=True)), "qasm", 3, perfil="balanceado")
    assert r["verificacao"]["metodo"] == "clifford" and r["verificacao"]["garantia"] == "exata"
    assert r["equivalente"] is True
    assert r["proposta"]["operacoes_dois_qubits"] == n - 1  # 3(n-1) CX -> n-1
    assert r["simulacao"]["metodo"] == "estabilizador"


def test_reescrita_prova_equivalencia_em_30_qubits_nao_clifford():
    qc = QuantumCircuit(30)
    for i in range(30):
        qc.rz(0.1 * (i + 1), i)
        qc.rz(0.2, i)
        qc.t(i)
        qc.tdg(i)
    for i in range(29):
        qc.cx(i, i + 1)
    r = processar_circuito(qasm2.dumps(qc), "qasm", 3, perfil="balanceado")
    assert r["equivalente"] is True and r["verificacao"]["metodo"] == "reescrita"
    assert r["simulacao"]["memoria_statevector_bytes"] == 16 * 2 ** 30


def test_estados_aleatorios_detectam_proposta_errada():
    a = random_circuit(12, 6, max_operands=2, seed=3)
    b = a.copy()
    b.rz(0.001, 5)  # diferença pequena, mas real
    resultado = verificacao.verificar(a, b)
    assert resultado["equivalente"] is False and resultado["metodo"] == "estados_aleatorios"
    assert verificacao.verificar(a, a.copy())["equivalente"] is True


def test_operador_detecta_diferenca_de_mapa_de_medicoes():
    a = QuantumCircuit(2, 2); a.h(0); a.measure(0, 0); a.measure(1, 1)
    b = QuantumCircuit(2, 2); b.h(0); b.measure(0, 1); b.measure(1, 0)
    assert verificacao.verificar(a, b)["equivalente"] is False


def test_circuito_dinamico_usa_verificacao_estatistica():
    a = QuantumCircuit(2, 1); a.h(0); a.measure(0, 0)
    with a.if_test((a.clbits[0], 1)):
        a.x(1)
    b = a.copy()
    r = verificacao.verificar(a, b)
    assert r["garantia"] == "estatistica" and r["equivalente"] is None
    assert r["detalhes"]["distribuicoes_compativeis"] is True


@pytest.mark.parametrize("topologia,esperado", [("linear", 7), ("anel", 8), ("grade", None), ("heavy_hex", None)])
def test_mapas_de_acoplamento_tem_exatamente_n_qubits_conexos(topologia, esperado):
    mapa = mapa_de_acoplamento(topologia, 8)
    assert mapa.size() == 8 and mapa.is_connected()
    if esperado:
        assert len({tuple(sorted(e)) for e in mapa.get_edges()}) == esperado


def test_hardware_respeita_conectividade_e_portas_nativas():
    qc = QuantumCircuit(5)
    qc.h(0)
    for alvo in range(1, 5):
        qc.cx(0, alvo)  # estrela: exige SWAPs numa linha
    r = processar_circuito(qasm2.dumps(qc), "qasm", 3, perfil="avancado",
                           hardware={"topologia": "linear", "portas_nativas": "ibm_cx"})
    saida = qasm2.loads(r["circuito_otimizado_qasm"], custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert set(saida.count_ops()) <= {"cx", "rz", "sx", "x"}
    for item in saida.data:
        if item.operation.num_qubits == 2:
            a, b = (saida.find_bit(q).index for q in item.qubits)
            assert abs(a - b) == 1
    assert r["equivalente"] is True and r["hardware"]["layout_inicial"] is not None


def test_analise_estrutural_e_metodo_de_simulacao():
    qc = QuantumCircuit(4, 1)
    qc.h(0); qc.h(0); qc.rz(0.1, 1); qc.rz(0.2, 1); qc.cx(0, 1); qc.cx(1, 2); qc.ccx(0, 1, 2)
    qc.measure(0, 0); qc.x(0)
    a = analisar_estrutura(qc)
    assert a["redundancias"]["pares_inversos_adjacentes"] == 1
    assert a["redundancias"]["rotacoes_fundiveis_adjacentes"] == 1
    assert a["medicoes_intermediarias"] is True and a["qubits_ociosos"] == [3]
    assert a["cx_equivalentes"] == 8 and a["gargalo"]["qubit"] == 1
    grande = analisar_estrutura(random_circuit(30, 10, max_operands=2, seed=9))
    escolha = escolher_metodo_simulacao(grande, 1536)
    assert escolha["exato"] is False or escolha["metodo"] == "matrix_product_state"
    assert escolha["memoria_statevector_bytes"] == 16 * 2 ** 30


def test_endpoint_optimize_aceita_perfil_e_hardware(client):
    qc = ghz(6, redundante=True)
    r = client.post("/qiskit/optimize", json={"code": qasm2.dumps(qc), "perfil": "avancado",
                                              "hardware": {"topologia": "linear", "portas_nativas": "ibm_heron"}})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["perfil"] == "avancado" and corpo["hardware"]["portas_nativas"] == ["cz", "rz", "sx", "x"]
    assert corpo["verificacao"]["metodo"] in ("operador", "clifford", "reescrita", "estados_aleatorios")
    r = client.post("/qiskit/optimize", json={"code": qasm2.dumps(qc), "perfil": "turbo"})
    assert r.status_code == 422


def test_qft16_em_hardware_e_equivalencia_aproximada_com_erro_medido():
    """O transpilador descarta rotações minúsculas da QFT; o motor não chama isso de prova."""
    from qiskit.circuit.library import QFTGate
    qc = QuantumCircuit(16)
    qc.append(QFTGate(16), range(16))
    r = processar_circuito(qasm2.dumps(qc.decompose()), "qasm", 3, perfil="balanceado",
                           hardware={"topologia": "linear", "portas_nativas": "ibm_cx"})
    v = r["verificacao"]
    assert r["equivalente"] is True and v["garantia"] in ("aproximada", "amostral", "exata")
    if v["garantia"] == "aproximada":
        assert 0 < v["detalhes"]["infidelidade_maxima"] <= 1e-8


def test_laco_extra_preserva_layout_do_roteamento():
    from quantum.otimizador import gerar_candidatos
    from quantum.qiskit_service import materializar_layout
    qc = QuantumCircuit(5)
    qc.h(0)
    for alvo in range(1, 5):
        qc.cx(0, alvo)
    candidatos, _, _ = gerar_candidatos(qc, "balanceado", 3, {"topologia": "linear"}, set())
    for nome, c, _ in candidatos:
        assert Operator(materializar_layout(qc, c)).equiv(Operator(qc)), nome
