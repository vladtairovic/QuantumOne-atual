---
id: qone_equivalencia
titulo: QuantumOne — Verificação de equivalência de circuitos
biblioteca: qiskit
temas: equivalencia,verificacao,otimizacao,simulacao
nivel: avancado
---

## Equivalência unitária até fase global (unitary equivalence, global phase)

Dois circuitos sem medições são equivalentes quando implementam a mesma unitária a menos de uma fase global e^{iφ}. Operator(a).equiv(Operator(b)) faz essa comparação; Operator(a) == Operator(b) exige igualdade exata, inclusive da fase. Exemplo: RZ(θ) e P(θ) diferem apenas por fase global, logo equiv é verdadeiro e == é falso.

```python validar
from qiskit import QuantumCircuit
from qiskit.quantum_info import Operator

a = QuantumCircuit(1)
a.rz(0.5, 0)
b = QuantumCircuit(1)
b.p(0.5, 0)
assert Operator(a).equiv(Operator(b))
assert not (Operator(a) == Operator(b))
```

## Métodos por tamanho e tipo de circuito

- Exata por unitária: até cerca de 10–12 qubits (a matriz tem 4ⁿ entradas).
- Exata para Clifford: comparação de tableaus (qiskit.quantum_info.Clifford), viável para dezenas ou centenas de qubits.
- Por vetores de estado: aplicar os dois circuitos a vários estados de entrada e comparar os resultados até fase global. Se todos coincidem, a evidência é forte mas probabilística (teste amostral), não uma prova formal; o custo é 16·2ⁿ bytes por estado.
- Com medições finais: remover as medições, comparar as distribuições de probabilidade exatas dos estados finais e conferir o mapeamento qubit→bit clássico.
- Com medições intermediárias, reset ou controle clássico: não existe uma unitária única. Use simulação com amostragem ou simulação de matriz densidade, e trate o resultado como verificação aproximada/estatística.

## Layout e permutações após roteamento

O transpilador pode permutar qubits (layout inicial e SWAPs finais). Para comparar o circuito transpilado com o original é preciso desfazer o layout, ou usar Operator.from_circuit, que considera o TranspileLayout. Comparar sem considerar o layout pode indicar falsamente que os circuitos diferem.

## Tipos de veredito

Relatórios de verificação devem dizer qual método foi usado e o que ele garante:

- "equivalente (exata)": prova por unitária ou por tableau de Clifford.
- "equivalente (amostral)": todos os estados de teste coincidiram; alta confiança, sem prova formal.
- "distribuições equivalentes": probabilidades de medição iguais; não garante igualdade das fases antes da medição.
- "não verificado": nenhum método foi aplicável dentro do orçamento; o motivo deve ser informado.
- "não equivalente": um contraexemplo foi encontrado (o circuito otimizado não deve ser usado).
