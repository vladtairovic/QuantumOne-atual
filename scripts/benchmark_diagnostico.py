"""Benchmark do motor de diagnóstico e correção.

Cada caso traz a entrada (danificada ou válida) e os códigos de problema
esperados. Mede: taxa de detecção (recall dos códigos esperados), precisão
(códigos reportados que eram esperados), correção validada (versão corrigida
aceita pelo carregador e sem erros na reanálise) e falsos positivos em
entradas válidas.

Uso: python scripts/benchmark_diagnostico.py --saida docs/benchmarks/diagnostico.json
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantum.diagnostico import diagnosticar  # noqa: E402

H = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
PY = "from qiskit import QuantumCircuit\n"
CASOS = [
    # OpenQASM
    {"id": "q_indice", "entrada": H + "qreg q[2];\nh q[2];\n", "esperados": ["INDEX_OUT_OF_RANGE"]},
    {"id": "q_registrador", "entrada": H + "qreg q[2];\nh r[0];\n", "esperados": ["UNDECLARED_REGISTER"]},
    {"id": "q_duplicado", "entrada": H + "qreg q[2];\ncx q[0],q[0];\n", "esperados": ["DUPLICATE_QUBIT"]},
    {"id": "q_angulo", "entrada": H + "qreg q[1];\nrz q[0];\n", "esperados": ["WRONG_PARAMETER_COUNT"]},
    {"id": "q_angulo_invalido", "entrada": H + "qreg q[1];\nrz(theta) q[0];\n", "esperados": ["INVALID_PARAMETER"]},
    {"id": "q_aridade", "entrada": H + "qreg q[2];\ncx q[0];\n", "esperados": ["WRONG_QUBIT_COUNT"]},
    {"id": "q_porta", "entrada": H + "qreg q[1];\nfoo q[0];\n", "esperados": ["UNKNOWN_GATE"]},
    {"id": "q_sinonimo", "entrada": H + "qreg q[2];\ncnot q[0],q[1];\n", "esperados": ["UNKNOWN_GATE"]},
    {"id": "q_ponto_virgula", "entrada": H + "qreg q[2];\nh q[0]\ncx q[0],q[1];\n", "esperados": ["MISSING_SEMICOLON"]},
    {"id": "q_cabecalho", "entrada": 'include "qelib1.inc";\nqreg q[1];\nh q[0];\n', "esperados": ["MISSING_HEADER"]},
    {"id": "q_include", "entrada": "OPENQASM 2.0;\nqreg q[1];\nh q[0];\n", "esperados": ["MISSING_INCLUDE"]},
    {"id": "q_medicao_tamanho", "entrada": H + "qreg q[3];\ncreg c[2];\nmeasure q -> c;\n", "esperados": ["MEASURE_SIZE_MISMATCH"]},
    {"id": "q_medicao_indice", "entrada": H + "qreg q[2];\ncreg c[2];\nmeasure q[1] -> c[5];\n", "esperados": ["INDEX_OUT_OF_RANGE"]},
    {"id": "q_ordem", "entrada": H + "h q[0];\nqreg q[1];\n", "esperados": ["USED_BEFORE_DECLARATION"]},
    {"id": "q_qasm3_em_2", "entrada": H + "qubit[2] q;\nh q[0];\n", "esperados": ["QASM3_SYNTAX"]},
    {"id": "q_qasm3", "entrada": 'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nbit[2] c;\nh q[0];\ncx q[0], q[1];\nc = measure q;\n',
     "esperados": ["QASM3_CONVERTIDO"]},
    {"id": "q_multiplos", "entrada": H + "qreg q[3];\ncreg c[3];\nh q[5];\ncx q[1],q[1];\nrx() q[0];\nfoo q[2];\nx q[0]\nmeasure q[0] -> c[9];\n",
     "esperados": ["INDEX_OUT_OF_RANGE", "DUPLICATE_QUBIT", "WRONG_PARAMETER_COUNT", "UNKNOWN_GATE", "MISSING_SEMICOLON", "INDEX_OUT_OF_RANGE"]},
    {"id": "q_include_externo", "entrada": 'OPENQASM 2.0;\ninclude "minhas_portas.inc";\ninclude "qelib1.inc";\nqreg q[1];\nh q[0];\n',
     "esperados": ["INVALID_INCLUDE"]},
    # Python / Qiskit
    {"id": "p_indice", "entrada": PY + "qc = QuantumCircuit(2)\nqc.h(3)\n", "esperados": ["INDEX_OUT_OF_RANGE"]},
    {"id": "p_duplicado", "entrada": PY + "qc = QuantumCircuit(2)\nqc.cx(1, 1)\n", "esperados": ["DUPLICATE_QUBIT"]},
    {"id": "p_cnot", "entrada": PY + "qc = QuantumCircuit(2)\nqc.cnot(0, 1)\n", "esperados": ["REMOVED_METHOD"]},
    {"id": "p_u3", "entrada": PY + "qc = QuantumCircuit(1)\nqc.u3(0.1, 0.2, 0.3, 0)\n", "esperados": ["REMOVED_METHOD"]},
    {"id": "p_import", "entrada": "qc = QuantumCircuit(1)\nqc.h(0)\n", "esperados": ["MISSING_IMPORT"]},
    {"id": "p_execute", "entrada": "from qiskit import QuantumCircuit, execute\nqc = QuantumCircuit(1, 1)\nqc.measure(0, 0)\n",
     "esperados": ["REMOVED_API"]},
    {"id": "p_aer", "entrada": "from qiskit import QuantumCircuit, Aer\nbackend = Aer.get_backend('qasm_simulator')\n",
     "esperados": ["REMOVED_API", "REMOVED_API"]},
    {"id": "p_c_if", "entrada": PY + "qc = QuantumCircuit(2, 1)\nqc.measure(0, 0)\nqc.x(1).c_if(0, 1)\n", "esperados": ["REMOVED_API"]},
    {"id": "p_medicao_sem_bits", "entrada": PY + "qc = QuantumCircuit(2)\nqc.measure(0, 0)\n", "esperados": ["MEASURE_WITHOUT_CLBITS"]},
    {"id": "p_argumentos", "entrada": PY + "qc = QuantumCircuit(1)\nqc.rx(0)\n", "esperados": ["WRONG_ARGUMENT_COUNT"]},
    {"id": "p_metodo", "entrada": PY + "qc = QuantumCircuit(1)\nqc.hadamard(0)\n", "esperados": ["UNKNOWN_METHOD"]},
    {"id": "p_sintaxe", "entrada": PY + "qc = QuantumCircuit(2)\nfor i in range(2)\n    qc.h(i)\n", "esperados": ["SYNTAX_ERROR"]},
    {"id": "p_multiplos", "entrada": "qc = QuantumCircuit(3, 3)\nqc.cnot(0, 1)\nqc.h(7)\nqc.cx(2, 2)\nqc.measure(0, 0)\n",
     "esperados": ["MISSING_IMPORT", "REMOVED_METHOD", "INDEX_OUT_OF_RANGE", "DUPLICATE_QUBIT"]},
    # Tracebacks
    {"id": "t_execute", "entrada": "Traceback (most recent call last):\n  File \"a.py\", line 1, in <module>\n    from qiskit import execute\nImportError: cannot import name 'execute' from 'qiskit'",
     "esperados": ["REMOVED_API"], "sem_codigo": True},
    {"id": "t_aer", "entrada": "ModuleNotFoundError: No module named 'qiskit_aer'", "esperados": ["MISSING_PACKAGE"], "sem_codigo": True},
    {"id": "t_atributo", "entrada": "AttributeError: 'QuantumCircuit' object has no attribute 'toffoli'", "esperados": ["REMOVED_METHOD"],
     "sem_codigo": True},
    # Válidos (não devem gerar erros)
    {"id": "v_qasm", "entrada": H + "qreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q -> c;\n", "esperados": []},
    {"id": "v_gate", "entrada": H + "gate minha(t) a, b { cx a, b; rz(t) b; cx a, b; }\nqreg q[2];\nminha(pi/4) q[0],q[1];\n", "esperados": []},
    {"id": "v_if", "entrada": H + "qreg q[2];\ncreg c[1];\nh q[0];\nmeasure q[0] -> c[0];\nif(c==1) x q[1];\n", "esperados": []},
    {"id": "v_python", "entrada": PY + "from math import pi\nqc = QuantumCircuit(3, 3)\nqc.h(0)\nqc.cx(0, 1)\nqc.rz(pi / 4, 2)\nqc.measure([0, 1, 2], [0, 1, 2])\n",
     "esperados": []},
    {"id": "v_registradores", "entrada": "from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister\nq = QuantumRegister(2, 'q')\nc = ClassicalRegister(2, 'c')\nqc = QuantumCircuit(q, c)\nqc.h(q[0])\nqc.cx(q[0], q[1])\nqc.measure(q, c)\n",
     "esperados": []},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    linhas = []
    for caso in CASOS:
        inicio = time.perf_counter()
        r = diagnosticar(caso["entrada"])
        tempo = time.perf_counter() - inicio
        reportados = Counter(p["codigo"] for p in r["problemas"] if p["severidade"] == "erro" or p["codigo"] == "QASM3_CONVERTIDO")
        esperados = Counter(caso["esperados"])
        acertos = sum((reportados & esperados).values())
        validado = None
        if not caso.get("sem_codigo"):
            v = r["validacao"]
            validado = bool(v.get("valido")) and v.get("erros_restantes_na_reanalise", 1) == 0
        linhas.append({"id": caso["id"], "esperados": caso["esperados"], "reportados": sorted(reportados.elements()),
                       "detectados": acertos, "total_esperado": sum(esperados.values()),
                       "falsos_positivos": sum((reportados - esperados).values()), "correcao_validada": validado,
                       "confianca_global": r["confianca_global"], "tempo_s": round(tempo, 4)})
        print(json.dumps(linhas[-1], ensure_ascii=False))
    danificados = [l for l in linhas if l["total_esperado"]]
    validos = [l for l in linhas if not l["total_esperado"]]
    com_codigo = [l for l in linhas if l["correcao_validada"] is not None]
    resumo = {
        "casos": len(linhas), "danificados": len(danificados), "validos": len(validos),
        "taxa_deteccao": round(sum(l["detectados"] for l in danificados) / sum(l["total_esperado"] for l in danificados), 4),
        "casos_com_todos_os_erros_detectados": sum(l["detectados"] == l["total_esperado"] for l in danificados),
        "precisao": round(sum(l["detectados"] for l in linhas) /
                          max(1, sum(l["detectados"] + l["falsos_positivos"] for l in linhas)), 4),
        "taxa_correcao_validada": round(sum(bool(l["correcao_validada"]) for l in com_codigo) / len(com_codigo), 4),
        "falsos_positivos_em_validos": sum(l["falsos_positivos"] for l in validos),
        "tempo_medio_ms": round(1000 * statistics.mean(l["tempo_s"] for l in linhas), 2),
    }
    relatorio = {"gerado_em": datetime.now(timezone.utc).isoformat(), "resumo": resumo, "casos": linhas}
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
