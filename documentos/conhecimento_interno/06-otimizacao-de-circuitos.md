---
id: qone_otimizacao_circuitos
titulo: QuantumOne — Padrões de otimização e transpilação de circuitos
biblioteca: qiskit
temas: otimizacao,transpilacao,roteamento,sintese,comutacao
nivel: avancado
---

## O que o transpilador faz (transpiler stages)

A transpilação do Qiskit é organizada em estágios: init (decomposição de portas de 3+ qubits), layout (escolha de qubits físicos), routing (inserção de SWAPs para respeitar a conectividade), translation (tradução para as portas nativas), optimization (simplificações) e scheduling. generate_preset_pass_manager(optimization_level=k, ...) monta essa sequência; transpile usa optimization_level=2 por padrão no Qiskit 2.x.

```python validar
import inspect
from qiskit.transpiler import generate_preset_pass_manager

assert inspect.signature(generate_preset_pass_manager).parameters["optimization_level"].default == 2
```

## Otimizações locais (gate cancellation, rotation merging)

- Cancelamento de inversos adjacentes: H·H, X·X, CX·CX e T·T† somem (InverseCancellation, CommutativeCancellation).
- Fusão de rotações: sequências de portas de um qubit são combinadas em uma única unitária e ressintetizadas (Optimize1qGatesDecomposition).
- Comutação: portas que comutam podem ser reordenadas para criar novos cancelamentos; por exemplo, portas diagonais comutam com o controle de um CX.
- Remoção de portas diagonais antes de medições (RemoveDiagonalGatesBeforeMeasure): Z, S, T e RZ imediatamente antes de medir na base computacional não alteram as probabilidades.
- Blocos de dois qubits: ConsolidateBlocks agrupa portas sobre o mesmo par e UnitarySynthesis reescreve o bloco com no máximo três CX.

```python validar
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Operator

qc = QuantumCircuit(2)
qc.h(0)
qc.h(0)
qc.rz(0.3, 1)
qc.rz(0.4, 1)
qc.cx(0, 1)
qc.cx(0, 1)
otimizado = transpile(qc, optimization_level=3, seed_transpiler=5)
assert otimizado.count_ops().get("cx", 0) == 0
assert Operator(otimizado).equiv(Operator(qc))
```

## Roteamento e conectividade (routing, SWAP, coupling map)

Quando o hardware só conecta certos pares de qubits (CouplingMap), operações entre qubits distantes exigem SWAPs; cada SWAP custa três CX. Boas escolhas de layout inicial (SabreLayout, VF2Layout) reduzem SWAPs. Topologias comuns: linha, anel, grade e heavy-hex (IBM). Após o roteamento, o circuito tem um layout (TranspileLayout) que mapeia qubits virtuais em físicos e pode terminar com os qubits permutados.

## Compromissos de otimização (trade-offs)

Nem toda otimização melhora todas as métricas ao mesmo tempo:

- Reduzir profundidade pode aumentar a contagem total de portas (paralelização com SWAPs ou decomposições diferentes).
- Traduzir para portas nativas aumenta a contagem (por exemplo, uma CP vira duas CX e rotações).
- Níveis mais altos de otimização custam mais tempo de compilação e são estocásticos: sementes diferentes (seed_transpiler) podem dar resultados diferentes; executar várias tentativas e escolher a melhor é uma estratégia válida.
- Em hardware, a métrica prioritária costuma ser a contagem de portas de dois qubits; em simulação, o tempo e a memória dependem mais do número de qubits e do emaranhamento.

## Otimização sem alterar a semântica

Uma otimização só é válida se o circuito resultante for equivalente ao original. Sem medições, a verificação é por unitária (até fase global) para poucos qubits, ou por métodos escaláveis (Clifford, simulação de estados de teste). Com medições finais, compare as distribuições de probabilidade. Com medições intermediárias, reset ou controle clássico, a comparação unitária não se aplica.
