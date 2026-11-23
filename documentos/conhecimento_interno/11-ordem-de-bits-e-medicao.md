---
id: qone_ordem_bits_medicao
titulo: QuantumOne — Ordem de bits (little-endian) e medição no Qiskit
biblioteca: qiskit
temas: ordem_de_bits,medicao,resultados,fundamentos
nivel: basico
---

## Convenção little-endian do Qiskit (bit ordering)

No Qiskit, o qubit 0 é o bit menos significativo. Em strings de resultado como "01", o caractere mais à direita corresponde ao bit clássico 0. Assim, aplicar X apenas no qubit 0 de um circuito de 2 qubits e medir produz "01", não "10". Muitas fontes de livros usam a convenção oposta (big-endian); misturar as duas é uma causa frequente de resultados que parecem invertidos.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

qc = QuantumCircuit(2)
qc.x(0)
assert Statevector(qc).probabilities_dict() == {"01": 1.0}
```

## Medição e registradores clássicos

qc.measure(q, c) grava o resultado do qubit q no bit clássico c. measure_all() adiciona uma barreira e um novo registrador "meas" com um bit por qubit. Com vários registradores clássicos, as contagens separam os registradores por espaço, do último para o primeiro registrador.

## Medições intermediárias e reset

Medir no meio do circuito colapsa o estado e permite controle clássico (if_test). reset força o qubit para |0⟩. Essas operações não são unitárias: um circuito com elas não tem matriz unitária e deve ser validado por simulação com amostragem ou matriz densidade.

## Medição em outras bases

Para medir na base X, aplique H antes de medir; na base Y, aplique S† e depois H. O valor esperado de um observável de Pauli pode ser estimado a partir das contagens nessa base ou calculado diretamente com o EstimatorV2.
