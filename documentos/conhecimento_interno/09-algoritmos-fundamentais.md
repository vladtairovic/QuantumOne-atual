---
id: qone_algoritmos
titulo: QuantumOne — Algoritmos quânticos fundamentais com exemplos validados
biblioteca: qiskit
temas: algoritmos,emaranhamento,grover,qft,deutsch_jozsa,bernstein_vazirani,vqe,qaoa,estimativa_de_fase
nivel: intermediario
---

## Estado de Bell e GHZ (entanglement)

O estado de Bell (|00⟩ + |11⟩)/√2 é preparado com H no qubit 0 e CX(0, 1). O estado GHZ de n qubits generaliza: H no qubit 0 seguido de uma cadeia de CX. Medindo, só aparecem as strings com todos os bits iguais, cada uma com probabilidade 1/2.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

n = 4
ghz = QuantumCircuit(n)
ghz.h(0)
for i in range(n - 1):
    ghz.cx(i, i + 1)
probs = Statevector(ghz).probabilities_dict()
assert set(probs) == {"0000", "1111"}
assert all(abs(p - 0.5) < 1e-9 for p in probs.values())
```

## Bernstein–Vazirani

Dado um oráculo f(x) = s·x (mod 2), o algoritmo descobre a string secreta s com uma única consulta: H em todos os qubits de entrada, ancilla em |−⟩, oráculo (CX de cada qubit i com s_i = 1 para a ancilla), H de novo e medição. O resultado é s com probabilidade 1.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

segredo = "1011"  # s_3 s_2 s_1 s_0 (ordem do Qiskit: qubit 0 à direita)
n = len(segredo)
qc = QuantumCircuit(n + 1)
qc.x(n)
qc.h(range(n + 1))
for i, bit in enumerate(reversed(segredo)):
    if bit == "1":
        qc.cx(i, n)
qc.h(range(n))
probs = Statevector(qc).probabilities_dict(qargs=range(n))
assert abs(probs[segredo] - 1.0) < 1e-9
```

## Deutsch–Jozsa

Decide se f: {0,1}ⁿ → {0,1} é constante ou balanceada com uma consulta. Após o algoritmo, medir todos os qubits de entrada em 0 indica função constante; qualquer outro resultado indica balanceada. Ele é útil como exemplo de interferência, não como vantagem prática.

## Grover (amplitude amplification)

Grover encontra um item marcado entre N = 2ⁿ com cerca de (π/4)·√N iterações de oráculo + difusor. Para 2 qubits e um único item marcado, uma iteração dá probabilidade 1. Iterações demais diminuem a probabilidade (o algoritmo "passa do ponto").

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

qc = QuantumCircuit(2)
qc.h([0, 1])
qc.cz(0, 1)          # oráculo: marca |11>
qc.h([0, 1])         # difusor
qc.x([0, 1])
qc.cz(0, 1)
qc.x([0, 1])
qc.h([0, 1])
assert abs(Statevector(qc).probabilities_dict()["11"] - 1.0) < 1e-9
```

## Transformada de Fourier quântica (QFT)

A QFT de n qubits usa O(n²) portas (Hadamards e fases controladas) mais SWAPs finais que invertem a ordem dos qubits. No Qiskit 2.x prefira qiskit.circuit.library.QFTGate; a classe QFT está obsoleta. A QFT aproximada (descartando rotações muito pequenas) reduz o custo com erro controlado.

```python validar
import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit.library import QFTGate
from qiskit.quantum_info import Operator

n = 3
qc = QuantumCircuit(n)
qc.append(QFTGate(n), range(n))
N = 2 ** n
dft = np.array([[np.exp(2j * np.pi * j * k / N) for k in range(N)] for j in range(N)]) / np.sqrt(N)
assert Operator(qc).equiv(Operator(dft))
```

## Estimativa de fase (QPE)

Estima a fase θ de um autovalor e^{2πiθ} de uma unitária U, usando t qubits de contagem, potências controladas U^(2^k) e QFT inversa. A precisão cresce com t; a QPE é a base do algoritmo de Shor e de algoritmos de química quântica.

## VQE e QAOA (algoritmos variacionais híbridos)

O VQE (Variational Quantum Eigensolver) minimiza ⟨ψ(θ)|H|ψ(θ)⟩ variando parâmetros θ de um ansatz com um otimizador clássico; o mínimo aproxima a energia do estado fundamental. O QAOA alterna camadas e^{−iγC} (custo) e e^{−iβB} (mistura) para problemas combinatórios como Max-Cut. Ambos dependem de: escolha do ansatz, número de camadas, otimizador clássico e ruído; platôs estéreis (barren plateaus) dificultam o treinamento de ansatzes profundos e aleatórios.

```python validar
import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp

theta = Parameter("theta")
ansatz = QuantumCircuit(1)
ansatz.ry(theta, 0)
hamiltoniano = SparsePauliOp("Z")
estimador = StatevectorEstimator()
valores = np.linspace(0, 2 * np.pi, 9)
energias = estimador.run([(ansatz, hamiltoniano, valores)]).result()[0].data.evs
assert abs(min(energias) - (-1.0)) < 1e-9
```
