---
id: qone_openqasm_2_3
titulo: QuantumOne — OpenQASM 2.0 e OpenQASM 3: estrutura, diferenças e erros comuns
biblioteca: openqasm
temas: openqasm2,openqasm3,interoperabilidade,depuracao
nivel: intermediario
---

## Estrutura mínima de um programa OpenQASM 2.0

Um programa OpenQASM 2.0 começa com a declaração de versão "OPENQASM 2.0;", normalmente seguida de include "qelib1.inc"; para importar a biblioteca padrão de portas. Registradores são declarados com qreg nome[n]; e creg nome[n];. Cada instrução termina com ponto e vírgula. Índices começam em 0: em qreg q[3]; os qubits válidos são q[0], q[1] e q[2].

```qasm validar
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0],q[1];
measure q -> c;
```

## Medição e broadcasting em OpenQASM 2.0

measure q[0] -> c[0]; mede um qubit. measure q -> c; mede o registrador inteiro, mas só é válido se os dois registradores tiverem o mesmo tamanho. Portas aplicadas a registradores inteiros (h q;) são expandidas para cada índice; em portas de dois qubits com registradores inteiros (cx a, b;) os tamanhos devem coincidir.

## Declarações de portas (gate, opaque)

gate nome(params) qargs { corpo } define uma porta a partir de outras. Parâmetros entre parênteses são ângulos; argumentos de qubit no corpo usam nomes, não índices. Portas opaque não têm definição e não podem ser otimizadas nem simuladas sem uma definição.

## Diferenças principais do OpenQASM 3 (OpenQASM 3 differences)

- Cabeçalho "OPENQASM 3.0;" (ou "OPENQASM 3;") e include "stdgates.inc"; em vez de qelib1.inc.
- Declarações qubit[n] q; e bit[n] c; (qreg/creg ainda são aceitos por compatibilidade na especificação).
- Medição como atribuição: c[0] = measure q[0]; ou c = measure q;.
- Controle clássico estruturado: if (c[0]) { x q[1]; } else { ... }, laços for e while, sub-rotinas def e tipos clássicos (int, uint, float, angle, bool).
- Temporização: delay, duration, stretch e box para controlar escalonamento.
- Modificadores de porta: ctrl @, negctrl @, inv @ e pow(k) @.

No Qiskit, qiskit.qasm2.loads/dumps tratam OpenQASM 2; qiskit.qasm3.dumps exporta OpenQASM 3 e qiskit.qasm3.loads depende do pacote opcional qiskit-qasm3-import.

```qasm
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
```

## Erros comuns em OpenQASM e como corrigir

- Falta do cabeçalho OPENQASM: o parser não reconhece a versão. Adicione "OPENQASM 2.0;" na primeira linha.
- Porta usada sem include "qelib1.inc": h, cx, rz etc. não estão definidas. Inclua a biblioteca ou declare a porta.
- Índice fora do registrador: q[3] em qreg q[3]; não existe (índices 0 a 2). Aumente o registrador ou corrija o índice, conforme o circuito pretendido.
- Registrador não declarado: usar r[0] sem qreg r[...]; declare o registrador antes do uso.
- Número errado de parâmetros: rz q[0]; falta o ângulo; u3(0.1) q[0]; tem parâmetros a menos (são três).
- Número errado de qubits: cx q[0]; precisa de dois operandos; h q[0],q[1]; aplica a um só qubit por vez.
- Mesmo qubit repetido: cx q[0],q[0]; é inválido porque controle e alvo precisam ser distintos.
- Ponto e vírgula ausente: cada instrução termina em ;.
- measure q -> c; com tamanhos diferentes: meça índice a índice ou ajuste os registradores.
- Sintaxe de OpenQASM 3 em arquivo declarado como 2.0 (qubit[2] q;, c = measure q;): mude o cabeçalho ou converta a sintaxe.
