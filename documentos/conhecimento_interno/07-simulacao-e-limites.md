---
id: qone_simulacao_limites
titulo: QuantumOne — Métodos de simulação e limites de recursos (até 30 qubits)
biblioteca: qiskit
temas: simulacao,statevector,estabilizadores,mps,redes_tensoriais,memoria
nivel: avancado
---

## Custo do vetor de estado (statevector memory)

Um vetor de estado de n qubits tem 2ⁿ amplitudes. Em precisão complex128 cada amplitude ocupa 16 bytes, logo a memória mínima é 16·2ⁿ bytes, sem contar cópias temporárias:

| Qubits | Memória mínima do vetor |
|---|---|
| 20 | 16 MiB |
| 24 | 256 MiB |
| 25 | 512 MiB |
| 26 | 1 GiB |
| 28 | 4 GiB |
| 30 | 16 GiB |

Com complex64 a memória cai pela metade, com perda de precisão. Operações de matriz unitária completa são muito mais caras: uma unitária de n qubits tem 4ⁿ entradas (16·4ⁿ bytes), por isso a comparação por Operator só é viável para poucos qubits (cerca de 10 a 12).

```python validar
def memoria_statevector_bytes(n, bytes_por_amplitude=16):
    return bytes_por_amplitude * 2 ** n

assert memoria_statevector_bytes(30) == 16 * 1024 ** 3
assert memoria_statevector_bytes(20) == 16 * 1024 ** 2
```

## Circuitos de Clifford e simulação estabilizadora (stabilizer, Gottesman-Knill)

Circuitos compostos apenas por portas de Clifford (H, S, S†, X, Y, Z, CX, CZ, SWAP), medições e reset podem ser simulados em tempo polinomial pelo formalismo estabilizador (teorema de Gottesman-Knill). No Qiskit, qiskit.quantum_info.Clifford representa a operação e StabilizerState o estado; o qiskit-aer oferece o método "stabilizer". Isso permite verificar equivalência exata de circuitos de Clifford com dezenas ou centenas de qubits.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Clifford

n = 30
qc = QuantumCircuit(n)
qc.h(0)
for i in range(n - 1):
    qc.cx(i, i + 1)
outro = QuantumCircuit(n)
outro.h(0)
for i in range(n - 1):
    outro.cx(i, i + 1)
    outro.cz(i, i + 1)
    outro.cz(i, i + 1)
assert Clifford(qc) == Clifford(outro)
```

## Matrix Product State (MPS) e redes tensoriais

O método MPS representa o estado como uma cadeia de tensores. O custo depende da dimensão de ligação (bond dimension), que cresce com o emaranhamento entre partes do circuito. Circuitos rasos ou com emaranhamento limitado (por exemplo, interações apenas entre vizinhos e poucas camadas) podem ser simulados com 30 ou mais qubits; circuitos aleatórios profundos fazem a dimensão de ligação crescer exponencialmente e perdem a vantagem. O qiskit-aer oferece method="matrix_product_state"; truncar valores singulares torna a simulação aproximada.

## Simulação por amostragem (shots)

Simular com shots produz contagens estatísticas. Com N amostras, a incerteza de uma probabilidade p é aproximadamente sqrt(p(1−p)/N). Comparar circuitos por amostragem é um teste estatístico, não uma prova: duas distribuições próximas dentro da tolerância não garantem equivalência exata.

## Escolha do método conforme o circuito

1. Se o circuito é de Clifford: simulação estabilizadora (exata, polinomial).
2. Se 2ⁿ·16 bytes cabem com folga na memória disponível: vetor de estado (exato).
3. Se o emaranhamento é limitado: MPS (exato sem truncamento; aproximado com truncamento).
4. Caso contrário: não há método exato viável; use simulação aproximada, reduza o circuito ou execute em hardware. Declare a limitação em vez de prometer simulação exata.
