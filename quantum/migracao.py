"""Regras de migração de APIs do Qiskit (0.x/1.x -> 2.x).

Cada regra traz um verificador executável: os testes confirmam, contra o
Qiskit instalado, que a API antiga não existe mais e que o substituto
existe. ``removido_em`` registra a versão segundo as notas de versão do
Qiskit; o verificador garante apenas a ausência na versão instalada.
"""
import importlib

REGRAS = [
    {"id": "execute", "padrao": r"from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*execute\b|\bqiskit\.execute\(|(?<![\w.])execute\(",
     "antigo": "qiskit.execute(...)", "novo": "transpile(...) + backend.run(...) ou primitivas (SamplerV2/EstimatorV2)",
     "removido_em": "1.0", "ausente": ("qiskit", "execute"), "presente": ("qiskit", "transpile")},
    {"id": "aer_no_qiskit", "padrao": r"from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*Aer\b|\bqiskit\.Aer\b|(?<![\w.])Aer\.get_backend",
     "antigo": "from qiskit import Aer / Aer.get_backend(...)", "novo": "from qiskit_aer import AerSimulator; AerSimulator()",
     "removido_em": "1.0", "ausente": ("qiskit", "Aer"), "presente": None},
    {"id": "basicaer", "padrao": r"\bBasicAer\b|qiskit\.providers\.basicaer",
     "antigo": "BasicAer", "novo": "qiskit.providers.basic_provider.BasicSimulator ou qiskit_aer",
     "removido_em": "1.0", "ausente": ("qiskit", "BasicAer"), "presente": ("qiskit.providers.basic_provider", "BasicSimulator")},
    {"id": "providers_aer", "padrao": r"qiskit\.providers\.aer",
     "antigo": "qiskit.providers.aer", "novo": "pacote qiskit_aer",
     "removido_em": "0.x", "ausente": ("modulo", "qiskit.providers.aer"), "presente": None},
    {"id": "cnot", "padrao": r"\.cnot\(", "antigo": "QuantumCircuit.cnot", "novo": "QuantumCircuit.cx",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "cnot"), "presente": ("QuantumCircuit", "cx"), "metodo": ("cnot", "cx")},
    {"id": "toffoli", "padrao": r"\.toffoli\(", "antigo": "QuantumCircuit.toffoli", "novo": "QuantumCircuit.ccx",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "toffoli"), "presente": ("QuantumCircuit", "ccx"), "metodo": ("toffoli", "ccx")},
    {"id": "fredkin", "padrao": r"\.fredkin\(", "antigo": "QuantumCircuit.fredkin", "novo": "QuantumCircuit.cswap",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "fredkin"), "presente": ("QuantumCircuit", "cswap"), "metodo": ("fredkin", "cswap")},
    {"id": "mct", "padrao": r"\.mct\(", "antigo": "QuantumCircuit.mct", "novo": "QuantumCircuit.mcx",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "mct"), "presente": ("QuantumCircuit", "mcx"), "metodo": ("mct", "mcx")},
    {"id": "identidade_i", "padrao": r"\.i\(", "antigo": "QuantumCircuit.i", "novo": "QuantumCircuit.id",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "i"), "presente": ("QuantumCircuit", "id"), "metodo": ("i", "id")},
    {"id": "u1", "padrao": r"\.u1\(", "antigo": "QuantumCircuit.u1(theta, q)", "novo": "QuantumCircuit.p(theta, q)",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "u1"), "presente": ("QuantumCircuit", "p"), "metodo": ("u1", "p")},
    {"id": "u2", "padrao": r"\.u2\(", "antigo": "QuantumCircuit.u2(phi, lam, q)", "novo": "QuantumCircuit.u(pi/2, phi, lam, q)",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "u2"), "presente": ("QuantumCircuit", "u")},
    {"id": "u3", "padrao": r"\.u3\(", "antigo": "QuantumCircuit.u3(theta, phi, lam, q)", "novo": "QuantumCircuit.u(theta, phi, lam, q)",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "u3"), "presente": ("QuantumCircuit", "u"), "metodo": ("u3", "u")},
    {"id": "cu1", "padrao": r"\.cu1\(", "antigo": "QuantumCircuit.cu1", "novo": "QuantumCircuit.cp",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "cu1"), "presente": ("QuantumCircuit", "cp"), "metodo": ("cu1", "cp")},
    {"id": "cu3", "padrao": r"\.cu3\(", "antigo": "QuantumCircuit.cu3(theta, phi, lam, c, t)",
     "novo": "QuantumCircuit.cu(theta, phi, lam, 0, c, t)",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "cu3"), "presente": ("QuantumCircuit", "cu")},
    {"id": "bind_parameters", "padrao": r"\.bind_parameters\(", "antigo": "QuantumCircuit.bind_parameters",
     "novo": "QuantumCircuit.assign_parameters", "removido_em": "1.0",
     "ausente": ("QuantumCircuit", "bind_parameters"), "presente": ("QuantumCircuit", "assign_parameters"),
     "metodo": ("bind_parameters", "assign_parameters")},
    {"id": "qasm_metodo", "padrao": r"\.qasm\(\s*\)", "antigo": "QuantumCircuit.qasm()", "novo": "qiskit.qasm2.dumps(circuito)",
     "removido_em": "1.0", "ausente": ("QuantumCircuit", "qasm"), "presente": ("qiskit.qasm2", "dumps")},
    {"id": "c_if", "padrao": r"\.c_if\(", "antigo": "Instruction.c_if(registrador, valor)",
     "novo": "with qc.if_test((registrador, valor)): ...", "removido_em": "2.0",
     "ausente": ("Instruction", "c_if"), "presente": ("QuantumCircuit", "if_test")},
    {"id": "pulse", "padrao": r"\bqiskit\.pulse\b|from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*pulse\b",
     "antigo": "qiskit.pulse", "novo": "não há substituto no Qiskit SDK 2.x", "removido_em": "2.0",
     "ausente": ("modulo", "qiskit.pulse"), "presente": None},
    {"id": "assemble", "padrao": r"from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*assemble\b|\bqiskit\.assemble\(",
     "antigo": "qiskit.assemble", "novo": "backend.run(circuitos) ou primitivas", "removido_em": "2.0",
     "ausente": ("qiskit", "assemble"), "presente": None},
    {"id": "opflow", "padrao": r"qiskit\.opflow", "antigo": "qiskit.opflow", "novo": "qiskit.quantum_info.SparsePauliOp",
     "removido_em": "1.0", "ausente": ("modulo", "qiskit.opflow"), "presente": ("qiskit.quantum_info", "SparsePauliOp")},
    {"id": "algorithms", "padrao": r"qiskit\.algorithms\b", "antigo": "qiskit.algorithms",
     "novo": "pacote separado qiskit_algorithms (mantido pela comunidade) ou implementação própria com primitivas",
     "removido_em": "1.0", "ausente": ("modulo", "qiskit.algorithms"), "presente": None},
    {"id": "quantum_instance", "padrao": r"\bQuantumInstance\b", "antigo": "qiskit.utils.QuantumInstance",
     "novo": "primitivas SamplerV2/EstimatorV2", "removido_em": "1.0",
     "ausente": ("qiskit.utils", "QuantumInstance"), "presente": ("qiskit.primitives", "StatevectorSampler")},
    {"id": "extensions", "padrao": r"qiskit\.extensions", "antigo": "qiskit.extensions",
     "novo": "qiskit.circuit.library", "removido_em": "1.0", "ausente": ("modulo", "qiskit.extensions"),
     "presente": ("qiskit.circuit", "library")},
    {"id": "tools", "padrao": r"qiskit\.tools\b", "antigo": "qiskit.tools (ex.: job_monitor)",
     "novo": "acompanhar job.status() ou utilitários do provedor", "removido_em": "1.0",
     "ausente": ("modulo", "qiskit.tools"), "presente": None},
    {"id": "test_mock", "padrao": r"qiskit\.test\.mock|qiskit\.test\b", "antigo": "qiskit.test.mock",
     "novo": "qiskit_ibm_runtime.fake_provider (backends falsos)", "removido_em": "1.0",
     "ausente": ("modulo", "qiskit.test"), "presente": None},
    {"id": "ibmq", "padrao": r"\bIBMQ\.|qiskit\.providers\.ibmq|from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*IBMQ\b",
     "antigo": "IBMQ / qiskit-ibmq-provider", "novo": "qiskit_ibm_runtime.QiskitRuntimeService", "removido_em": "descontinuado",
     "ausente": ("qiskit", "IBMQ"), "presente": None},
    {"id": "get_statevector_execute", "padrao": r"get_statevector\(",
     "antigo": "result.get_statevector() após execute com statevector_simulator",
     "novo": "qiskit.quantum_info.Statevector(circuito_sem_medicoes)", "removido_em": "fluxo antigo",
     "ausente": None, "presente": ("qiskit.quantum_info", "Statevector"), "aviso_apenas": True},
]


def _objeto(alvo):
    escopo, nome = alvo
    if escopo == "modulo":
        return importlib.import_module(nome)
    if escopo == "QuantumCircuit":
        from qiskit import QuantumCircuit
        return getattr(QuantumCircuit, nome)
    if escopo == "Instruction":
        from qiskit.circuit import Instruction
        return getattr(Instruction, nome)
    return getattr(importlib.import_module(escopo), nome)


def existe(alvo) -> bool:
    try:
        _objeto(alvo)
        return True
    except (ImportError, AttributeError):
        return False


def por_id(identificador):
    return next(r for r in REGRAS if r["id"] == identificador)
