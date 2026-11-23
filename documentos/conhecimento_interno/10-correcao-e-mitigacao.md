---
id: qone_correcao_mitigacao
titulo: QuantumOne — Correção de erros, ruído e mitigação
biblioteca: geral
temas: correcao_de_erros,ruido,mitigacao,estabilizadores,surface_code
nivel: avancado
---

## Correção versus mitigação de erros (error correction vs mitigation)

Correção de erros quânticos (QEC) codifica um qubit lógico em vários qubits físicos e detecta/corrige erros com medições de síndrome, sem medir a informação lógica. Exige muitos qubits e operações abaixo de um limiar de erro. Mitigação de erros não corrige o estado: ela reduz o viés de valores esperados por pós-processamento estatístico (por exemplo, extrapolação para ruído zero, ZNE; cancelamento probabilístico de erros, PEC; correção de erros de leitura) ao custo de mais execuções.

## Código de repetição de bit-flip

O código de 3 qubits codifica |0⟩→|000⟩ e |1⟩→|111⟩ com dois CX. Medindo as paridades Z0Z1 e Z1Z2 com dois qubits auxiliares, identifica-se qual qubit sofreu um X sem revelar o valor lógico. Ele corrige um único bit-flip, mas não protege contra erros de fase; o código de Shor (9 qubits) e os códigos estabilizadores gerais protegem ambos.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

qc = QuantumCircuit(5)
qc.x(0)            # estado lógico |1>
qc.cx(0, 1)
qc.cx(0, 2)        # codificado em |111>
qc.x(1)            # erro de bit-flip no qubit 1
qc.cx(0, 3)
qc.cx(1, 3)        # síndrome Z0Z1 no qubit 3
qc.cx(1, 4)
qc.cx(2, 4)        # síndrome Z1Z2 no qubit 4
probs = Statevector(qc).probabilities_dict(qargs=[3, 4])
assert probs == {"11": 1.0}  # ambas as paridades violadas -> erro no qubit 1
```

## Códigos estabilizadores e surface code

Um código estabilizador é definido por um grupo de operadores de Pauli que deixam os estados lógicos invariantes; medir esses operadores (síndromes) revela erros. O surface code dispõe qubits em uma grade 2D com estabilizadores locais do tipo X e Z; tem limiar de erro relativamente alto (da ordem de 1%) e é um dos principais candidatos para computação tolerante a falhas. A distância d do código determina quantos erros podem ser corrigidos: até ⌊(d−1)/2⌋.

## Modelos de ruído (noise models)

Ruídos comuns: despolarizante, amortecimento de amplitude (T1), desfasamento (T2), erros de leitura e erros coerentes (rotações extras). No qiskit-aer, NoiseModel combina erros por porta e por qubit; simulações ruidosas usam matrizes densidade ou trajetórias amostradas e não devem ser apresentadas como execuções em hardware real.

## Supressão de erros na compilação

Técnicas aplicadas antes da execução: escolher qubits com menor erro, reduzir a contagem de portas de dois qubits e a profundidade, dynamical decoupling (sequências de pulsos em qubits ociosos) e twirling de Pauli (converte erros coerentes em estocásticos).
