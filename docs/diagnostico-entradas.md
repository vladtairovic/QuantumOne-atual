# Diagnóstico das entradas de 16/11/2026

Os IDs `8343f2a5-33b6-4f0d-a100-2cc872764732` e
`0d1c43ca-95b8-4ae5-979a-d046a3ecdd27` chegaram à API e retornaram,
respectivamente, `INVALID_CIRCUIT` e `UNSUPPORTED_PYTHON`.
O parser interrompe a análise no primeiro erro; corrigir esse erro pode
revelar outros problemas na entrada.

## QASM enviado

| Instrução | Problema |
|---|---|
| `h q[12]` | `qreg q[10]` permite apenas índices de 0 a 9 |
| `cx q[1],q[1]` | Controle e alvo precisam ser qubits diferentes |
| `swap q[3],q[15]` | `q[15]` não existe |
| `rx() q[4]` | Falta o ângulo |
| `rz(abc) q[6]` | `abc` não foi definido |
| `unknown_gate q[7]` | Porta não declarada |
| `ccx q[0],q[2],q[14]` | `q[14]` não existe |
| `measure q[9] -> c[12]` | `creg c[10]` permite apenas índices de 0 a 9 |

O exemplo `exemplos/circuito_validado_10q.qasm` remove as sete operações
inválidas e destina a última medição a `c[9]`. Ele demonstra uma entrada
aceita; não é uma reconstrução da intenção do autor nem uma transformação
com equivalência ao código original inválido.

## Python enviado

`QuantumCircuit(6, 6)` tem qubits e bits de 0 a 5. São inválidos
`qc.cx(0, 0)`, `qc.h(8)`, `qc.ccx(1, 2, 9)`, `qc.swap(4, 7)` e
`qc.measure(9, 5)`.

`Parameter` é válido no Qiskit, mas o interpretador desta aplicação aceita
apenas ângulos numéricos. `print(qc.draw())` também fica fora do subconjunto
de construção aceito. Essas limitações não são erros de sintaxe Python.
Não são executados scripts Python arbitrários.

O exemplo `exemplos/circuito_validado_6q.py` mantém apenas as instruções
suportadas e válidas. Foram removidos o import e a criação de `Parameter`,
`qc.ry(theta, 1)`, as cinco operações inválidas e `print(qc.draw())`.
Nenhum valor foi inventado para `theta`. Esse exemplo não é equivalente
ao programa enviado.

## Consulta de limites

Os logs do proxy registraram conexão recusada enquanto a API reiniciava.
Em uma das ocorrências, a API iniciou às 17:29:39 e ficou pronta às
17:30:05 (America/Sao_Paulo); o frontend tentou consultá-la às 17:29:56.
A consulta passou a ter até sete tentativas, com esperas de 1, 2, 4, 8, 8
e 8 segundos, dentro do prazo inicial de 45 segundos da interface.
O POST de otimização não é repetido automaticamente.

## Validação da correção

- 167 testes Python aprovados, um teste optativo ignorado.
- 10 testes do serviço de frontend aprovados, incluindo recuperação de falha
  de conexão, cancelamento e limite de tentativas.
- Build de produção Next.js/TypeScript concluído.
- Verificação na interface local com Qiskit real: o exemplo Python gerou
  métricas para 6 qubits e 18 operações; o QASM, para 10 qubits e 44 operações.
  A interface também mostrou os novos erros de índice e de Parameter.
- A equivalência unitária não foi confirmada nesses exemplos: há medições e,
  no QASM, a quantidade de qubits excede o orçamento da verificação matricial.
