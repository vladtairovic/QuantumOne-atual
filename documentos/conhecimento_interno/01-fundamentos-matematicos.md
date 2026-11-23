---
id: qone_fundamentos_matematicos
titulo: QuantumOne — Fundamentos matemáticos da computação quântica
biblioteca: geral
temas: fundamentos,algebra_linear,numeros_complexos,estados,produto_tensorial
nivel: basico
---

## Números complexos e amplitudes (complex numbers, amplitudes)

Um estado quântico é descrito por amplitudes complexas. Um número complexo z = a + bi tem módulo |z| = sqrt(a² + b²) e fase arg(z). A probabilidade de observar um resultado é o quadrado do módulo da amplitude correspondente (regra de Born, Born rule): p = |amplitude|².

Multiplicar todas as amplitudes por um mesmo fator e^{iφ} (fase global, global phase) não altera nenhuma probabilidade nem nenhuma medição. Já a fase relativa entre amplitudes altera a interferência e, portanto, os resultados após portas adicionais.

## Qubit e notação de Dirac (qubit, Dirac notation, ket)

Um qubit é um vetor unitário em C²: |ψ⟩ = α|0⟩ + β|1⟩, com |α|² + |β|² = 1. Na base computacional, |0⟩ = (1, 0)ᵀ e |1⟩ = (0, 1)ᵀ. O bra ⟨ψ| é o transposto conjugado do ket |ψ⟩, e ⟨φ|ψ⟩ é o produto interno.

Estados de superposição importantes: |+⟩ = (|0⟩ + |1⟩)/√2 e |−⟩ = (|0⟩ − |1⟩)/√2, obtidos aplicando a porta Hadamard (H) a |0⟩ e |1⟩.

```python validar
from qiskit.quantum_info import Statevector
from qiskit import QuantumCircuit
import numpy as np

qc = QuantumCircuit(1)
qc.h(0)
estado = Statevector(qc)
assert np.allclose(estado.data, [1 / np.sqrt(2), 1 / np.sqrt(2)])
assert np.isclose(sum(abs(a) ** 2 for a in estado.data), 1.0)
```

## Vários qubits e produto tensorial (tensor product, kron)

O estado de n qubits vive em um espaço de dimensão 2ⁿ. Estados de qubits independentes combinam-se pelo produto tensorial ⊗ (numpy.kron). Para n qubits, o vetor de estado tem 2ⁿ amplitudes: é por isso que a simulação exata por vetor de estado cresce exponencialmente.

Um estado é emaranhado (entangled) quando não pode ser escrito como produto tensorial de estados individuais. O estado de Bell (|00⟩ + |11⟩)/√2 é o exemplo mínimo.

## Operadores unitários (unitary operators)

Portas quânticas são matrizes unitárias U, isto é, U†U = I. Unitárias preservam a norma do estado e são reversíveis: o inverso de U é U†. Medições e reset não são unitários; por isso circuitos com medições intermediárias não podem ser comparados apenas por sua matriz unitária.

Composição: aplicar A e depois B corresponde ao produto matricial B·A (a ordem é invertida em relação à ordem de escrita no circuito).

```python validar
import numpy as np
from qiskit.quantum_info import Operator
from qiskit import QuantumCircuit

qc = QuantumCircuit(2)
qc.h(0)
qc.cx(0, 1)
u = Operator(qc).data
assert np.allclose(u.conj().T @ u, np.eye(4))
```

## Autovalores e observáveis (eigenvalues, observables, Hermitian)

Um observável é um operador hermitiano (H = H†). Seus autovalores são reais e são os valores possíveis de uma medição. O valor esperado em um estado |ψ⟩ é ⟨ψ|H|ψ⟩. Operadores de Pauli (X, Y, Z) têm autovalores +1 e −1. No Qiskit, observáveis compostos por somas de Paulis são representados por SparsePauliOp.

```python validar
from qiskit.quantum_info import SparsePauliOp, Statevector
from qiskit import QuantumCircuit
import numpy as np

bell = QuantumCircuit(2)
bell.h(0)
bell.cx(0, 1)
zz = SparsePauliOp("ZZ")
valor = Statevector(bell).expectation_value(zz)
assert np.isclose(valor, 1.0)
```
