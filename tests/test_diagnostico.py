"""Prioridade 3: interpretação, diagnóstico de múltiplos erros, correção e validação."""
import pytest
from fastapi.testclient import TestClient
from qiskit import qasm2

from main import app
from quantum.diagnostico import diagnosticar
from quantum.diagnostico_qasm import QELIB1
from rag.interpretacao import interpretar

QASM_DANIFICADO = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[10];
creg c[10];
h q[0];
h q[12];
cx q[1],q[1];
swap q[3],q[15];
rx() q[4];
rz(abc) q[6];
unknown_gate q[7];
ccx q[0],q[2],q[14];
cnot q[0],q[1];
x q[2]
measure q[9] -> c[12];
measure q[0] -> c[0];
"""

PY_DANIFICADO = """from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
theta = Parameter("theta")
qc = QuantumCircuit(6, 6)
qc.h(0)
qc.cx(0, 0)
qc.h(8)
qc.ccx(1, 2, 9)
qc.swap(4, 7)
qc.ry(theta, 1)
qc.measure(9, 5)
print(qc.draw())
"""


def codigos(resultado):
    return [p["codigo"] for p in resultado["problemas"]]


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_tabela_qelib1_confere_com_o_parser_do_qiskit():
    for nome, (n_params, n_qubits) in QELIB1.items():
        # u0 é mapeada pelo Qiskit para um atraso e exige comprimento inteiro.
        valor = "1" if nome == "u0" else "0.1"
        params = f"({', '.join([valor] * n_params)})" if n_params else ""
        operandos = ",".join(f"q[{i}]" for i in range(n_qubits))
        qasm2.loads(f'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[5];\n{nome}{params} {operandos};\n',
                    custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def test_qasm_lista_todos_os_erros_em_uma_unica_analise():
    r = diagnosticar(QASM_DANIFICADO)
    assert r["linguagem"] == "qasm"
    esperados = ["INDEX_OUT_OF_RANGE", "DUPLICATE_QUBIT", "INDEX_OUT_OF_RANGE", "WRONG_PARAMETER_COUNT",
                 "INVALID_PARAMETER", "UNKNOWN_GATE", "INDEX_OUT_OF_RANGE", "UNKNOWN_GATE", "MISSING_SEMICOLON",
                 "INDEX_OUT_OF_RANGE"]
    assert codigos(r) == esperados
    linhas = [p["linha"] for p in r["problemas"]]
    assert linhas == sorted(linhas) and linhas[0] == 6
    assert r["validacao"]["valido"] is True and r["validacao"]["erros_restantes_na_reanalise"] == 0
    assert r["confianca_global"] == "baixa"  # há decisões de intenção indeterminável
    assert "cx q[0],q[1];" in r["codigo_corrigido"]  # cnot -> cx é inequívoco
    assert "x q[2];" in r["codigo_corrigido"]       # ponto e vírgula restaurado
    assert "--- original" in r["diff"] and "+++ corrigido" in r["diff"]


def test_qasm_indeterminavel_traz_alternativas_e_nao_inventa_angulo():
    r = diagnosticar(QASM_DANIFICADO)
    angulo = next(p for p in r["problemas"] if p["codigo"] == "WRONG_PARAMETER_COUNT")
    assert angulo["confianca"] == "baixa" and angulo["correcao"]["tipo"] == "remocao"
    assert angulo["alternativas"] and "rx(" not in r["codigo_corrigido"]
    indice = next(p for p in r["problemas"] if p["codigo"] == "INDEX_OUT_OF_RANGE")
    assert len(indice["alternativas"]) == 2  # ampliar registrador OU corrigir o índice


def test_politica_expandir_registradores_preserva_instrucoes():
    r = diagnosticar(QASM_DANIFICADO, politica="expandir_registradores")
    corrigido = qasm2.loads(r["codigo_corrigido"], custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert corrigido.num_qubits == 16 and corrigido.num_clbits == 13
    assert "h q[12];" in r["codigo_corrigido"] and "swap q[3],q[15];" in r["codigo_corrigido"]
    assert r["validacao"]["valido"] is True


def test_qasm_sem_cabecalho_include_e_declaracao_tardia():
    r = diagnosticar("h q[0];\ncx q[0],q[1];\nqreg q[2];\n", formato="qasm")
    assert set(codigos(r)) >= {"MISSING_HEADER", "MISSING_INCLUDE", "USED_BEFORE_DECLARATION"}
    circuito = qasm2.loads(r["codigo_corrigido"], custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    assert circuito.num_qubits == 2 and circuito.count_ops() == {"h": 1, "cx": 1}


def test_openqasm3_basico_e_convertido_e_validado():
    r = diagnosticar('OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nbit[2] c;\nh q[0];\ncx q[0], q[1];\nc = measure q;\n')
    assert "QASM3_CONVERTIDO" in codigos(r)
    assert r["validacao"]["valido"] is True
    assert r["validacao"]["metricas"]["tipos_operacoes"] == {"h": 1, "cx": 1, "measure": 2}


def test_python_multiplos_erros_parameter_e_instrucoes_ignoradas():
    r = diagnosticar(PY_DANIFICADO)
    assert r["linguagem"] == "python"
    assert codigos(r).count("INDEX_OUT_OF_RANGE") == 4 and "DUPLICATE_QUBIT" in codigos(r)
    assert "UNBOUND_PARAMETERS" in codigos(r)
    assert r["validacao"]["valido"] is True
    assert any("print" in i["texto"] for i in r["validacao"]["instrucoes_ignoradas"])
    assert "qc.h(8)" not in r["codigo_corrigido"] and "qc.ry(theta, 1)" in r["codigo_corrigido"]


def test_python_qiskit_antigo_migrado_automaticamente():
    legado = ("from qiskit import QuantumCircuit, execute, Aer\nqc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cnot(0, 1)\n"
              "qc.measure([0, 1], [0, 1])\nbackend = Aer.get_backend('qasm_simulator')\n"
              "job = execute(qc, backend, shots=1024)\n")
    r = diagnosticar(legado)
    corrigido = r["codigo_corrigido"]
    assert "from qiskit_aer import AerSimulator" in corrigido and "backend = AerSimulator()" in corrigido
    assert "backend.run(transpile(qc, backend), shots=1024)" in corrigido and "qc.cx(0, 1)" in corrigido
    assert "execute" not in corrigido.replace("backend.run", "")
    assert "migracao_de_versao" in r["resumo"]["categorias"]
    compile(corrigido, "corrigido.py", "exec")


def test_python_import_ausente_e_medicao_sem_bits():
    r = diagnosticar("qc = QuantumCircuit(2)\nqc.h(0)\nqc.measure(0, 0)\n", formato="python")
    assert set(codigos(r)) >= {"MISSING_IMPORT", "MEASURE_WITHOUT_CLBITS"}
    assert r["codigo_corrigido"].startswith("from qiskit import QuantumCircuit")
    assert "QuantumCircuit(2, 2)" in r["codigo_corrigido"] and r["validacao"]["valido"] is True


def test_python_varios_erros_de_sintaxe():
    r = diagnosticar("from qiskit import QuantumCircuit\nqc = QuantumCircuit(3)\nfor i in range(3)\n    qc.h(i)\nprint 'ok'\n",
                     formato="python")
    sintaxe = [p for p in r["problemas"] if p["codigo"] == "SYNTAX_ERROR"]
    assert [p["linha"] for p in sintaxe] == [3, 5]
    compile(r["codigo_corrigido"], "x.py", "exec")


@pytest.mark.parametrize("traceback_texto,codigo", [
    ("Traceback (most recent call last):\n  File \"a.py\", line 1, in <module>\n    from qiskit import execute\n"
     "ImportError: cannot import name 'execute' from 'qiskit'", "REMOVED_API"),
    ("Traceback (most recent call last):\n  File \"a.py\", line 4, in <module>\n    qc.cnot(0, 1)\n"
     "AttributeError: 'QuantumCircuit' object has no attribute 'cnot'", "REMOVED_METHOD"),
    ("ModuleNotFoundError: No module named 'qiskit_aer'", "MISSING_PACKAGE"),
    ("qiskit.circuit.exceptions.CircuitError: 'Index 5 out of range for size 3.'", "INDEX_OUT_OF_RANGE"),
    ("NameError: name 'QuantumCircuit' is not defined", "UNDEFINED_NAME"),
])
def test_tracebacks_comuns(traceback_texto, codigo):
    r = diagnosticar(traceback_texto)
    assert r["problemas"] and r["problemas"][0]["codigo"] == codigo
    assert r["problemas"][0]["correcao"]["descricao"]


def test_codigo_valido_nao_gera_falso_positivo():
    valido = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q -> c;\n'
    r = diagnosticar(valido)
    assert r["problemas"] == [] and r["confianca_global"] == "alta" and r["validacao"]["valido"]
    py = "from qiskit import QuantumCircuit\nqc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure([0, 1], [0, 1])\n"
    assert diagnosticar(py)["problemas"] == []


def test_interpretacao_detecta_tipo_intencao_e_biblioteca():
    assert interpretar("O que é emaranhamento?")["tipo_entrada"] == "linguagem_natural"
    r = interpretar("Por que meu código dá erro?\n```python\nfrom qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\n```")
    assert r["intencao"] == "depuracao" and r["biblioteca_principal"] == "qiskit"
    assert interpretar("Como otimizar a profundidade deste circuito?")["intencao"] == "otimizacao"
    assert interpretar("qreg q[2];\nh q[0];")["hipoteses"]


def test_endpoint_diagnose_isola_execucao_e_nao_executa_codigo(client):
    malicioso = "import os\nos.system('echo invadido')\nqc = QuantumCircuit(1)\n"
    r = client.post("/qiskit/diagnose", json={"code": malicioso})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["linguagem"] == "python" and "MISSING_IMPORT" in codigos(corpo)
    r = client.post("/qiskit/diagnose", json={"code": QASM_DANIFICADO, "politica": "expandir_registradores"})
    assert r.status_code == 200 and r.json()["validacao"]["valido"] is True
    assert client.post("/qiskit/diagnose", json={"code": "x", "politica": "outra"}).status_code == 422
