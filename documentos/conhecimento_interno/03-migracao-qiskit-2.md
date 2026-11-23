---
id: qone_migracao_qiskit2
titulo: QuantumOne — Migração para o Qiskit 2.x (APIs removidas e substitutos)
biblioteca: qiskit
temas: migracao,depuracao,versoes,erros
nivel: intermediario
---

## Como reconhecer código escrito para Qiskit antigo (deprecated, removed)

Muitos exemplos na internet foram escritos para o Qiskit 0.x. No Qiskit 2.x esses códigos falham com ImportError (por exemplo "cannot import name 'execute' from 'qiskit'") ou AttributeError ("'QuantumCircuit' object has no attribute 'cnot'"). A ausência de cada API listada aqui foi conferida no Qiskit 2.6 pela suíte de testes do QuantumOne.

## Execução de circuitos: execute e Aer (execute removed, qiskit_aer)

- from qiskit import execute — removido. Use transpile(circuito, backend) seguido de backend.run(...), ou as primitivas SamplerV2/EstimatorV2.
- from qiskit import Aer / Aer.get_backend('qasm_simulator') — removido. Instale qiskit-aer e use from qiskit_aer import AerSimulator.
- BasicAer — removido. Para um simulador simples sem dependências, use qiskit.providers.basic_provider.BasicSimulator.
- Simulação exata de estado: em vez de executar no statevector_simulator e chamar get_statevector, construa Statevector(circuito) com o circuito sem medições.

```python validar
from qiskit import QuantumCircuit, transpile
from qiskit.providers.basic_provider import BasicSimulator

qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])
backend = BasicSimulator()
resultado = backend.run(transpile(qc, backend), shots=200, seed_simulator=7).result()
contagens = resultado.get_counts()
assert set(contagens) <= {"00", "11"} and sum(contagens.values()) == 200
```

## Métodos de QuantumCircuit removidos (cnot, toffoli, u3, bind_parameters)

| Antigo | Substituto no Qiskit 2.x |
|---|---|
| qc.cnot(c, t) | qc.cx(c, t) |
| qc.toffoli(a, b, t) | qc.ccx(a, b, t) |
| qc.fredkin(c, a, b) | qc.cswap(c, a, b) |
| qc.mct(controles, alvo) | qc.mcx(controles, alvo) |
| qc.i(q) | qc.id(q) |
| qc.u1(λ, q) | qc.p(λ, q) |
| qc.u2(φ, λ, q) | qc.u(π/2, φ, λ, q) |
| qc.u3(θ, φ, λ, q) | qc.u(θ, φ, λ, q) |
| qc.cu1(λ, c, t) | qc.cp(λ, c, t) |
| qc.cu3(θ, φ, λ, c, t) | qc.cu(θ, φ, λ, 0, c, t) |
| qc.bind_parameters(valores) | qc.assign_parameters(valores) |
| qc.qasm() | qiskit.qasm2.dumps(qc) |

Observação: em arquivos OpenQASM 2 com include "qelib1.inc", as portas u1, u2 e u3 continuam válidas; o que foi removido são os métodos Python de QuantumCircuit.

```python validar
import math
from qiskit import QuantumCircuit, qasm2
from qiskit.quantum_info import Operator

novo = QuantumCircuit(1)
novo.u(math.pi / 2, 0.1, 0.2, 0)
antigo_qasm = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\nu2(0.1,0.2) q[0];\n'
assert Operator(qasm2.loads(antigo_qasm)).equiv(Operator(novo))
assert not hasattr(QuantumCircuit, "u2")
```

## Controle clássico: c_if removido (if_test)

Instruction.c_if(registrador, valor) foi removido no Qiskit 2.0. Use o gerenciador de contexto QuantumCircuit.if_test((registrador_ou_bit, valor)). Circuitos com controle clássico não têm uma matriz unitária única; sua validação exige simulação com medições.

```python validar
from qiskit import QuantumCircuit, ClassicalRegister, QuantumRegister

q = QuantumRegister(2, "q")
c = ClassicalRegister(1, "c")
qc = QuantumCircuit(q, c)
qc.h(0)
qc.measure(0, c[0])
with qc.if_test((c, 1)):
    qc.x(1)
assert any(i.operation.name == "if_else" for i in qc.data)
```

## Módulos removidos (pulse, opflow, algorithms, QuantumInstance)

- qiskit.pulse — removido no Qiskit 2.0, sem substituto no SDK.
- qiskit.opflow — removido; use qiskit.quantum_info.SparsePauliOp para operadores e observáveis.
- qiskit.algorithms — saiu do Qiskit; existe o pacote separado qiskit_algorithms, mantido pela comunidade. Alternativa: implementar o laço variacional com primitivas e scipy.optimize.
- qiskit.utils.QuantumInstance — removido; use primitivas.
- qiskit.extensions — removido; as portas ficam em qiskit.circuit.library.
- qiskit.tools (por exemplo job_monitor) e qiskit.test.mock — removidos.
- IBMQ / qiskit-ibmq-provider — descontinuado; use qiskit_ibm_runtime.QiskitRuntimeService.
- qiskit.assemble e execução com Qobj — removidos no Qiskit 2.0.

## Primitivas V2 (SamplerV2, EstimatorV2)

As primitivas V2 recebem PUBs (primitive unified blocs): tuplas (circuito, parâmetros) para o Sampler, ou (circuito, observáveis, parâmetros) para o Estimator. Localmente, StatevectorSampler e StatevectorEstimator fazem simulação exata sem qiskit-aer.

```python validar
from qiskit import QuantumCircuit
from qiskit.primitives import StatevectorSampler

qc = QuantumCircuit(2)
qc.h(0)
qc.cx(0, 1)
qc.measure_all()
resultado = StatevectorSampler(seed=11).run([qc], shots=100).result()
contagens = resultado[0].data.meas.get_counts()
assert set(contagens) <= {"00", "11"} and sum(contagens.values()) == 100
```
