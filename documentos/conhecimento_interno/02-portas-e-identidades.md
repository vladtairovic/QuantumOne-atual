---
id: qone_portas_identidades
titulo: QuantumOne — Portas quânticas e identidades verificáveis
biblioteca: qiskit
temas: portas,identidades,otimizacao,equivalencia
nivel: intermediario
---

## Portas de um qubit (single-qubit gates)

As portas de Pauli X, Y e Z, a Hadamard H, as portas de fase S (√Z) e T (√S), e as rotações RX(θ), RY(θ), RZ(θ) formam o vocabulário básico. A porta P(λ) (phase) aplica a fase e^{iλ} apenas ao estado |1⟩; RZ(λ) é igual a P(λ) a menos de uma fase global e^{−iλ/2}.

A porta U(θ, φ, λ) representa qualquer unitária de um qubit (a menos de fase global). No Qiskit 2.x, os antigos métodos u1, u2 e u3 não existem mais: use p, u e u com os mesmos ângulos.

## Identidades usadas por otimizadores (gate identities)

Todas as identidades abaixo são verificadas pela suíte de testes do QuantumOne com qiskit.quantum_info.Operator.equiv (igualdade a menos de fase global):

- H·H = I, X·X = I, Z·Z = I, CX·CX = I (portas autoinversas).
- S·S = Z, T·T = S, S·S† = I, T·T† = I.
- H·X·H = Z e H·Z·H = X (a Hadamard troca as bases X e Z).
- RZ(a)·RZ(b) = RZ(a + b); o mesmo vale para RX e RY no mesmo eixo.
- RZ(λ) ≡ P(λ) a menos de fase global.
- CZ = (I⊗H)·CX·(I⊗H): Hadamards no alvo convertem CX em CZ.
- SWAP = CX(0,1)·CX(1,0)·CX(0,1): cada SWAP custa três CX em hardware que só tem CX.
- CZ é simétrica: CZ(0,1) = CZ(1,0).
- Portas diagonais (Z, S, T, RZ, P, CZ) comutam entre si.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator


def equivalentes(a, b):
    return Operator(a).equiv(Operator(b))


def circ(n, *ops):
    qc = QuantumCircuit(n)
    for nome, args in ops:
        getattr(qc, nome)(*args)
    return qc


vazio1, vazio2 = QuantumCircuit(1), QuantumCircuit(2)
assert equivalentes(circ(1, ("h", [0]), ("h", [0])), vazio1)
assert equivalentes(circ(2, ("cx", [0, 1]), ("cx", [0, 1])), vazio2)
assert equivalentes(circ(1, ("s", [0]), ("s", [0])), circ(1, ("z", [0])))
assert equivalentes(circ(1, ("t", [0]), ("t", [0])), circ(1, ("s", [0])))
assert equivalentes(circ(1, ("h", [0]), ("x", [0]), ("h", [0])), circ(1, ("z", [0])))
assert equivalentes(circ(1, ("rz", [0.3, 0]), ("rz", [0.4, 0])), circ(1, ("rz", [0.7, 0])))
assert equivalentes(circ(1, ("rz", [0.9, 0])), circ(1, ("p", [0.9, 0])))
assert equivalentes(circ(2, ("h", [1]), ("cx", [0, 1]), ("h", [1])), circ(2, ("cz", [0, 1])))
assert equivalentes(circ(2, ("cx", [0, 1]), ("cx", [1, 0]), ("cx", [0, 1])), circ(2, ("swap", [0, 1])))
assert equivalentes(circ(2, ("cz", [0, 1])), circ(2, ("cz", [1, 0])))
assert equivalentes(circ(2, ("cz", [0, 1]), ("t", [0])), circ(2, ("t", [0]), ("cz", [0, 1])))
```

## Portas de dois qubits e custo (two-qubit gates, CX count)

Em hardware atual, portas de dois qubits têm erro e duração muito maiores que portas de um qubit. Por isso a métrica mais importante de custo costuma ser a contagem de portas de dois qubits (CX, CZ, ECR) e a profundidade de duas portas, não o total de portas.

Uma unitária arbitrária de dois qubits pode ser decomposta com no máximo três CX mais rotações de um qubit (decomposição KAK). Blocos longos de portas atuando sobre o mesmo par de qubits podem, portanto, ser reescritos com no máximo três CX (ConsolidateBlocks + UnitarySynthesis).

## Portas multi-controladas (multi-controlled gates)

CCX (Toffoli) decomposta em CX e portas de um qubit exige 6 CX na construção padrão. Portas com muitos controles (MCX) crescem rapidamente em custo; qubits auxiliares (ancillas) reduzem esse custo. Ao otimizar, verifique se o circuito usa MCX desnecessariamente.

```python validar
from qiskit import QuantumCircuit, transpile

qc = QuantumCircuit(3)
qc.ccx(0, 1, 2)
decomposto = transpile(qc, basis_gates=["cx", "u"], optimization_level=3, seed_transpiler=1)
assert decomposto.count_ops()["cx"] == 6
```
