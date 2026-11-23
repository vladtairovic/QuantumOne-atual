---
id: qone_erros_depuracao
titulo: QuantumOne — Padrões de erro e estratégias de depuração em Qiskit
biblioteca: qiskit
temas: depuracao,erros,traceback,python
nivel: intermediario
---

## Método de depuração em etapas (debugging strategy)

1. Leia a última linha do traceback: ela traz o tipo da exceção e a mensagem. As linhas "File ..., line N" mostram onde o erro ocorreu; a última linha com o seu arquivo é normalmente o ponto a corrigir.
2. Classifique o erro: importação (ImportError/ModuleNotFoundError), API inexistente (AttributeError), uso inválido de registradores e índices (CircuitError, IndexError), tipos/valores (TypeError, ValueError), parsing OpenQASM (QASM2ParseError) ou execução (falta de medições, backend).
3. Reduza o caso: remova portas até o erro desaparecer; o último trecho removido contém a causa.
4. Valide cada correção com uma verificação objetiva: o código passa a construir o circuito, a equivalência com o original é confirmada ou as contagens têm o formato esperado.

## ImportError e ModuleNotFoundError

- "No module named 'qiskit_aer'": o simulador Aer é um pacote separado (pip install qiskit-aer).
- "cannot import name 'execute' from 'qiskit'" ou "cannot import name 'Aer' from 'qiskit'": código do Qiskit 0.x. Veja a migração para Qiskit 2.x.
- "No module named 'qiskit.providers.aer'": caminho antigo; use qiskit_aer.
- Import ausente: usar QuantumCircuit sem from qiskit import QuantumCircuit gera NameError.

## AttributeError em QuantumCircuit

"'QuantumCircuit' object has no attribute 'cnot'" (ou toffoli, u3, bind_parameters, qasm) indica método removido. Substitua pelo equivalente atual (cx, ccx, u, assign_parameters, qiskit.qasm2.dumps).

```python validar
from qiskit import QuantumCircuit

qc = QuantumCircuit(2)
try:
    qc.cnot(0, 1)
except AttributeError as erro:
    assert "cnot" in str(erro)
else:
    raise AssertionError("cnot deveria não existir no Qiskit 2.x")
qc.cx(0, 1)
```

## Índices de qubits e bits fora do limite (index out of range)

QuantumCircuit(n) cria qubits de 0 a n−1. Acessar qc.h(n) gera CircuitError com a mensagem "Index n out of range for size n". Em OpenQASM 2, o parser informa que o índice está fora do registrador. A correção depende da intenção: aumentar o circuito (se faltou um qubit) ou corrigir o índice (se foi erro de digitação). Quando a intenção não pode ser determinada, apresente as duas alternativas.

```python validar
from qiskit import QuantumCircuit
from qiskit.circuit.exceptions import CircuitError

qc = QuantumCircuit(2)
try:
    qc.x(2)
except CircuitError as erro:
    assert "out of range" in str(erro)
else:
    raise AssertionError("índice 2 deveria ser inválido em 2 qubits")
```

## Mesmo qubit como controle e alvo (duplicate qubit arguments)

qc.cx(0, 0) gera CircuitError ("duplicate qubit arguments"). Uma porta controlada precisa de qubits distintos. Verifique se o índice foi copiado por engano.

```python validar
from qiskit import QuantumCircuit
from qiskit.circuit.exceptions import CircuitError

qc = QuantumCircuit(2)
try:
    qc.cx(0, 0)
except CircuitError as erro:
    assert "duplicate" in str(erro).lower()
else:
    raise AssertionError("cx(0, 0) deveria ser rejeitado")
```

## Medições ausentes e contagens vazias (no counts, missing measurements)

Circuitos sem medição não produzem contagens no Sampler ou em backend.run. Adicione qc.measure_all() (cria um registrador clássico "meas") ou qc.measure(qubits, bits) com um registrador clássico declarado. Para obter o vetor de estado exato, não meça: use Statevector(qc).

## Medir em bits inexistentes

qc.measure(0, 0) em QuantumCircuit(2) sem bits clássicos falha porque não há clbit 0. Crie o circuito com QuantumCircuit(2, 2) ou use measure_all.

```python validar
from qiskit import QuantumCircuit
from qiskit.circuit.exceptions import CircuitError

qc = QuantumCircuit(2)
try:
    qc.measure(0, 0)
except CircuitError:
    pass
else:
    raise AssertionError("medir sem bits clássicos deveria falhar")
corrigido = QuantumCircuit(2, 2)
corrigido.measure(0, 0)
```

## Parâmetros simbólicos não atribuídos (unbound parameters)

Circuitos com Parameter precisam de valores antes de simular ou executar em backends: use qc.assign_parameters({theta: 0.5}) ou envie os valores na PUB das primitivas. Erros que mencionam parâmetros não atribuídos (unbound parameters) indicam valores faltando.

## Ordem dos bits nos resultados

Se as contagens parecem "invertidas" (por exemplo "01" quando se esperava "10"), provavelmente é a convenção little-endian do Qiskit: o qubit 0 é o caractere mais à direita. Isso não é erro de execução.
