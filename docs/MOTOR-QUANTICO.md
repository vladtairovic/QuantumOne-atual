# Motor de interpretação, diagnóstico, correção e otimização

## Diagnóstico e correção (`POST /qiskit/diagnose`)

Entrada: `{"code": "...", "format": "auto|qasm|python", "politica": "conservadora|expandir_registradores"}`.
Aceita OpenQASM 2, OpenQASM 3 (subconjunto básico), Python/Qiskit, tracebacks e textos que misturam
explicação, código e erro (blocos ` ``` ` são extraídos).

Pipeline (`quantum/diagnostico.py`):
1. detecta o tipo, a linguagem, a versão e a biblioteca (`rag/interpretacao.py`);
2. infere a intenção e registra hipóteses quando a entrada é ambígua;
3. analisa a sintaxe e a semântica **de todas as linhas** (o parser do Qiskit para no primeiro erro);
4. aponta a causa raiz de cada problema, com linha e trecho;
5. propõe correções e alternativas;
6. **valida** a versão corrigida com os carregadores reais do otimizador e a reanalisa;
7. devolve diff, limitações e grau de confiança.

### Problemas reconhecidos
- **OpenQASM**: cabeçalho ausente/inválido, include ausente ou externo, registrador não declarado,
  duplicado ou usado antes da declaração, índice fora do registrador, porta desconhecida (com
  sinônimos: `cnot`→`cx`, `toffoli`→`ccx`…), número errado de parâmetros ou de qubits, parâmetro
  inválido (identificador indefinido), qubit repetido, medição mal formada ou de tamanhos diferentes,
  ponto e vírgula ausente, sintaxe de OpenQASM 3 em arquivo 2.0, conversão de OpenQASM 3.
- **Python/Qiskit** (AST, sem executar): vários erros de sintaxe (com correções simples para `print`
  antigo, `:` ausente e parênteses), imports ausentes, APIs e métodos removidos (27 regras de
  `quantum/migracao.py`), índices fora do circuito, qubit repetido, medição sem bits clássicos,
  número errado de argumentos, método inexistente, `Parameter` sem valores.
- **Tracebacks**: pacote ausente, import removido, atributo removido, índice fora do limite, qubits
  repetidos, medições ausentes, parâmetros não atribuídos, falha de tradução do transpilador, parse
  de QASM e `NameError`.

### Política de correção
- Correções **inequívocas** são aplicadas automaticamente e têm confiança `alta` (cabeçalho, include,
  ponto e vírgula, renomeações de API, migração de `execute`/`Aer`/`c_if`/`qasm()`).
- Quando a intenção **não pode ser determinada** (índice inexistente, ângulo ausente, porta
  desconhecida, qubit repetido), o problema traz alternativas e confiança `baixa`. A versão corrigida
  segue a política:
  - `conservadora` (padrão): remove a instrução; o resultado é um circuito válido, não uma
    reconstrução garantida da intenção;
  - `expandir_registradores`: amplia o registrador para caber o índice.
- Nenhum ângulo, porta ou qubit é inventado. As categorias separam `correcao_de_codigo`,
  `migracao_de_versao`, `conversao_de_linguagem` e `diagnostico_de_execucao`. Mudanças de algoritmo
  não são feitas pelo diagnóstico, e a otimização é uma etapa separada.

## Otimização (`POST /qiskit/optimize`)

Novos campos: `perfil` (`rapido`, `balanceado`, `avancado`, `experimental`; ausente mantém o
comportamento antigo, com um `transpile` no `optimization_level`), `prioridade` (`dois_qubits`,
`profundidade` ou `total`) e `hardware` (`topologia`: `linear`, `anel`, `grade`, `heavy_hex` ou
`todos`; `portas_nativas`: `cx_u`, `ibm_cx`, `ibm_heron` ou `ibm_eagle`).

| Perfil | Estratégias |
|---|---|
| rápido | transpile nível 1 |
| balanceado | níveis 2 e 3 + laço extra (Optimize1q, CommutativeCancellation, InverseCancellation, ConsolidateBlocks, UnitarySynthesis até ponto fixo) |
| avançado | balanceado + 5 sementes (com hardware) + ressíntese de Clifford |
| experimental | nível 3 com 20 sementes + todas as estratégias (custo alto, ganho não garantido) |

A escolha ordena os candidatos pela prioridade (padrão: CX equivalentes, depois profundidade, depois
total) e aceita o primeiro **não refutado** pela verificação. Sem hardware-alvo, o circuito original
é candidato, então o resultado nunca piora a métrica prioritária. Com hardware, a saída usa a
numeração física, e o relatório traz `layout_inicial` e `layout_final`.

Otimização ZX não foi integrada: exigiria uma dependência nova (pyzx) sem benefício medido no
conjunto de portas do projeto. O documento 06 do conhecimento interno a descreve como técnica.

## Verificação de equivalência (`quantum/verificacao.py`)

| Método | Quando | Garantia |
|---|---|---|
| `operador` | até 8 qubits (`MAX_EQUIVALENCE_QUBITS`, teto 12) e 1.000 operações | exata, até fase global |
| `clifford` | ambos de Clifford (H, S, CX, CZ, SWAP, Paulis…) | exata, dezenas de qubits |
| `reescrita` | U·V† é simplificado à identidade sem permutação residual | exata (prova por reescrita) |
| `estados_aleatorios` | até 22 qubits e dentro de `QONE_STATEVECTOR_BUDGET` | amostral; contraexemplo é conclusivo |
| `estados_aleatorios` com `aproximada` | infidelidade ≤ 1e-8 e fase ≤ 1e-4 rad | diferença real, mas desprezível, e medida |
| `amostragem` | medição intermediária, reset, controle clássico (até 20 qubits) | estatística (TVD por shots) |
| `identidade` | o original foi mantido | exata |

Medições finais são separadas, e o mapeamento qubit→bit é comparado. A equivalência aproximada existe
porque o transpilador do Qiskit pode descartar rotações muito pequenas: na QFT de 16 qubits
roteada para uma linha, a infidelidade medida foi 5,7×10⁻¹⁰. O motor relata isso em vez de chamar
de prova.

## Análise estrutural e recursos

`analise_original`/`analise_final`: qubits, bits, profundidade, profundidade de 2 qubits, contagens
por tipo, portas de 2 e de 3+ qubits, **CX equivalentes** (custo após decompor para {cx, u}),
medições (inclusive intermediárias), resets, barreiras, controle clássico, parâmetros, qubits
ociosos, pares que interagem, grau máximo, gargalo (qubit com mais portas de 2 qubits), Clifford e
redundâncias adjacentes (pares inversos, rotações fundíveis, diagonais antes de medir).

`simulacao`: método recomendado conforme o circuito e a memória do worker:

| Situação | Método |
|---|---|
| circuito de Clifford | estabilizador (exato, polinomial) |
| 3 × 16·2ⁿ bytes cabem no orçamento | statevector (exato) |
| interações locais ou poucas camadas | MPS (exato sem truncamento; qiskit-aer) |
| caso contrário | nenhum método exato viável (alternativas listadas) |

**Não há simulação exata universal de 30 qubits**: um statevector de 30 qubits em complex128 ocupa
16 GiB só de amplitudes. O motor processa, otimiza e, quando um dos métodos acima se aplica,
verifica circuitos de até 30 qubits (32 é o limite configurado de entrada).

## Segurança e estabilidade
- Nenhum código do usuário é executado: QASM é analisado, e Python é interpretado por um subconjunto
  restrito (sem imports arbitrários, atributos, funções ou E/S).
- Diagnóstico e otimização rodam no worker descartável com limites de memória (`RLIMIT_AS`), CPU,
  arquivos e tempo, e com concorrência limitada (`MAX_CONCURRENT_OPTIMIZATIONS`).
- Respostas JSON do worker são serializadas com conversão segura de tipos numpy.
- Falhas viram erros tipados (`INVALID_CIRCUIT`, `UNSUPPORTED_PYTHON`, `OPTIMIZATION_TIMEOUT`…) sem
  derrubar a API.
