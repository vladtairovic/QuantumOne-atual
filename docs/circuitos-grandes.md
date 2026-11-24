# Análise e otimização de circuitos grandes

## Diagnóstico reproduzido em 16/11/2026

Antes da alteração, no Docker local com Qiskit 2.6.0:

| Entrada válida | Resultado anterior | Origem |
|---|---|---|
| 47 operações, 20 qubits | 200 | Sucesso |
| 501 operações, código abaixo de 10.000 caracteres | 422 HTTP_ERROR | Worker: MAX_OPERACOES_QASM=500 |
| 2.000 linhas, 1.997 operações, 20 qubits | 422 INVALID_INPUT, body.qasm | Pydantic: max_length=10000, antes do handler |
| JSON com code, sem qasm | 422 INVALID_INPUT | Contrato anterior aceitava apenas qasm |
| QASM inválido | 422 HTTP_ERROR | Parser, mascarado pela mesma mensagem de capacidade |

O frontend também bloqueava 10.000 caracteres e encaminhava somente QASM ao Qiskit; Python seguia para o LLM. Não havia limite de corpo específico na API. O proxy Next usava 120 s, o worker 30 s. Portanto o 422 não identifica sozinho sua causa: sem o payload histórico não é possível atribuir a linha de log original a uma única dessas condições. Os casos acima foram reproduzidos, com respostas e logs, antes de editar o código.

## Contrato compatível

`POST /qiskit/optimize` mantém `{ "qasm": "OPENQASM 2.0; ...", "optimization_level": 3 }`.

Novo contrato: `{ "code": "from qiskit import QuantumCircuit\nqc=QuantumCircuit(20)\nqc.h(0)", "format": "python", "optimization_level": 3 }`.

Informe exatamente um de `qasm` e `code`. `format` pode ser `auto`, `qasm` ou `python`; o padrão é `auto`. O campo legado `qasm` sempre significa QASM2. O nível é inteiro de 0 a 3; booleanos são rejeitados. Conteúdo vazio é erro de circuito. O código não é aparado antes de contar bytes e linhas.

Os campos antigos permanecem: `original`, `proposta`, `equivalente`, `status_verificacao`, `motivo`, diferenças e indicadores de redução, `circuito_otimizado_qasm`. Foram acrescentados formato detectado/de saída, referência SHA-256 da entrada UTF-8, tempo de processamento no worker (parsing, otimização e exportação; exclui inicialização do processo), nível, passes efetivamente executados, reduções observadas e avisos. Passes executados não implicam redução. Matrizes completas são usadas somente no orçamento pequeno de equivalência; circuitos grandes retornam `equivalente: null`, nunca uma equivalência inventada.

`total_operacoes` e `profundidade` seguem `QuantumCircuit.size/depth` da versão fixada; barreiras não entram na contagem de operações, mas entram no limite de instruções. `instrucoes` conta as instruções de topo, `tipos_operacoes` inclui barreiras, `bits_classicos` e `operacoes_dois_qubits` complementam a análise. Controle clássico permanece como instrução `if_else` nas métricas de topo; o orçamento também percorre seus corpos. Portas QASM definidas pelo usuário são expandidas, com orçamento verificado antes da expansão. A profundidade é validada novamente após expandir. Medições, registradores, barreiras e condicionais não são removidos indiscriminadamente.

A saída é OpenQASM 2, inclusive para entrada Python. O editor troca o formato ao aplicar a proposta. Se a exportação não for representável, a API devolve métricas, saída vazia e aviso. Permutações de layout são materializadas em portas antes da exportação, pois QASM não carrega o layout do SDK. Extensões do exportador são decompostas em u3/cx quando necessário; métricas refletem o circuito final, podendo aumentar a contagem.

## Configuração central

`quantum/config.py` lê o ambiente. `GET /qiskit/limits` expõe os valores efetivos. Todos os valores devem ser positivos e finitos; configuração inválida deve ser corrigida pelo operador.

| Variável | Padrão | Significado |
|---|---:|---|
| MAX_CIRCUIT_LINES | 2500 | Linhas físicas, incluindo comentários |
| MAX_QUBITS | 32 | Qubits somados entre registradores |
| MAX_CLASSICAL_BITS | 256 | Bits clássicos |
| MAX_CIRCUIT_GATES | 10000 | Instruções, incluindo diretivas e expansão de definições |
| MAX_CIRCUIT_DEPTH | 10000 | Profundidade Qiskit |
| MAX_CODE_SIZE_BYTES | 524288 | Bytes UTF-8 do código |
| OPTIMIZATION_TIMEOUT_SECONDS | 60 | Prazo total incluindo inicialização do worker |
| MAX_CONCURRENT_OPTIMIZATIONS | 1 | Processos simultâneos por worker da API, sem fila ilimitada |
| QISKIT_WORKER_MEMORY_MB | 1536 | Espaço de endereçamento do subprocesso Linux (RLIMIT_AS) |
| MAX_PYTHON_STEPS | 200000 | Orçamento AST, interpretação e laços |
| MAX_EQUIVALENCE_QUBITS | 8 | Orçamento matricial; teto de segurança absoluto de 10 |
| MAX_EQUIVALENCE_OPERATIONS | 1000 | Orçamento de operações da equivalência |
| API_MEMORY_LIMIT | 2g | Memória do serviço API no Compose |
| API_CPU_LIMIT | 2 | CPUs do serviço API |

Sem `OPTIMIZATION_TIMEOUT_SECONDS`, execução direta aceita a antiga `QISKIT_TIMEOUT_SECONDS` como fallback. O Compose usa a variável nova. Edite `.env` e reconstrua/recrie API e frontend: `docker compose up -d --build api frontend`. O timeout do proxy é calculado no build do Next a partir dos argumentos Compose (maior prazo API/Qiskit + 15 s). O navegador consulta o prazo efetivo. Os limites não são codificados novamente no editor. A pergunta textual tem orçamento separado, 18.000 caracteres; circuitos acima de 8.000 caracteres chegam ao LLM como trechos inicial/final e métricas estruturadas. O Qiskit recebe o código completo, sem depender de RAG/LLM/Chroma.

O orçamento JSON é derivado: seis vezes o limite de código mais 16 KiB para permitir escapes JSON. Um middleware ASGI limita bytes acumulados inclusive sem Content-Length. A API roda com um worker Uvicorn e limite de 64 conexões concorrentes; mudar a quantidade de workers multiplica o orçamento de subprocessos. Não aumente concorrência sem medir a memória do modelo RAG e do Qiskit. Neste host, antes das mudanças, a API usava aproximadamente 989 MiB, frontend 106 MiB. O limite de 2 GiB da API não aumenta a memória alocada pelo Docker Desktop.

## Python permitido e segurança

Não há exec/eval, compilação ou importação arbitrária de código recebido. AST interpretada permite:

- `from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister`, aliases; `qiskit.circuit`; `import qiskit`/`import math`.
- Um circuito por entrada, por contagens posicionais ou registradores. Nomes de registradores são strings curtas.
- Números finitos de módulo até 10^12; operações +, -, *, /, // e % sobre números; `math.pi`, `tau`, `e`, sin/cos/tan/sqrt/exp/log.
- Listas/tuplas, índices/fatias de registradores, `range`, atribuições e `for` limitados por passos. Sem multiplicação de listas ou objetos.
- Métodos posicionais: h/x/y/z/id, s/sdg/t/tdg/sx/sxdg, rx/ry/rz/p/u, cx/cy/cz/ch/cp/crx/cry/crz/cu, ccx/ccz/cswap/swap, rxx/ryy/rzz/rzx/ecr/dcx/iswap, reset/measure/measure_all/barrier.

Não é um executor geral de programas Python. Funções/classes/comprehensions, while, imports de filesystem/rede, atributos arbitrários, parâmetros simbólicos e métodos fora da lista retornam diagnóstico de subconjunto não suportado. Operações válidas para a linguagem Python podem não pertencer a esse subconjunto. QASM permite somente `qelib1.inc`; extensões legadas são reconhecidas sem substituir definições explícitas do usuário. Nenhum include arbitrário é lido.

O subprocesso recebe ambiente mínimo, sem credenciais de LLM. Threads numéricas e paralelismo Qiskit são limitados. Linux aplica orçamento CPU, memória, arquivos e descritores; timeout/cancelamento mata e coleta o processo, inclusive durante criação. Esses limites de SO não existem na execução Windows nativa; use Docker para as garantias de recursos. A segurança do Python vem da interpretação restrita, não de assumir que um subprocesso comum seja uma sandbox de código arbitrário. O Compose limita processos a 128, remove capabilities e impede ganho de privilégios, preservando a rede necessária ao RAG.

## Erros

A API preserva `request_id`, `code`, `message`, `fields` no topo e acrescenta `error: {code, message, details}`. Logs distinguem etapa de requisição/domínio sem registrar o circuito inteiro nem credenciais.

| HTTP | Código | Categoria |
|---:|---|---|
| 400 | INVALID_JSON | JSON malformado |
| 415 | CONTENT_TYPE | Tipo de conteúdo incorreto |
| 422 | INVALID_REQUEST | Campos/tipos/formato incompatíveis |
| 422 | INVALID_CIRCUIT | Sintaxe/índices/semântica inválidos, com linha e motivo quando disponíveis |
| 422 | UNSUPPORTED_PYTHON | Construção fora do subconjunto seguro |
| 422 | UNSUPPORTED_OPERATION | Porta opaca sem definição para otimização |
| 413 | CIRCUIT_LIMIT_EXCEEDED / REQUEST_TOO_LARGE | Limite configurado excedido |
| 429 | OPTIMIZER_BUSY | Capacidade concorrente ocupada |
| 499 | CLIENT_DISCONNECTED | Conexão encerrada pelo cliente; worker cancelado (registro interno) |
| 504 | OPTIMIZATION_TIMEOUT | Prazo excedido; processo encerrado |
| 503 | WORKER_RESOURCE_ERROR | Falha de processo/orçamento de recursos |
| 500 | OPTIMIZATION_FAILED / EQUIVALENCE_FAILED | Falha interna ou proposta não equivalente |

## Verificação reproduzível

```sh
docker build --target test -t quantumone-capacity-test .
docker run --rm --network none --memory 2g --cpus 2 --pids-limit 128 quantumone-capacity-test python -m pytest -q -p no:cacheprovider
docker compose config --quiet
docker compose build api frontend
```

`tests/test_circuit_capacity.py` gera entradas válidas de 50, 500 e 2.000 linhas em ambos os formatos, 20 qubits e 5.000 operações por broadcast. Verifica limites por ambiente, UTF-8, erros JSON/schema/sintaxe, portas, equivalência de pequenas matrizes, semântica de definições próprias, medições/barreiras/controle clássico, laços restritos e cancelamento/sobrecarga reais. Testes legados foram preservados com alterações explícitas apenas no status de limites (413) e no catálogo OpenAPI.

A cobertura também inclui 3.000 linhas e 33 qubits com configuração ampliada, portas opacas, literais de registrador fora da faixa e desconexão TCP real contra Uvicorn. O teste legado que rejeitava 501 operações passou a validar aceitação no padrão e rejeição com MAX_CIRCUIT_GATES=500.

Para repetir a verificação HTTP sem chamar LLM nem escrever no Chroma:

```sh
python scripts/verificar_circuitos_http.py --base-url http://127.0.0.1:8000 --report artifacts/quantum-capacity-api.json
python scripts/verificar_circuitos_http.py --base-url http://127.0.0.1:3000/api --report artifacts/quantum-capacity-proxy.json
```

Frontend: compilar `services/analysis-api.ts` para `/tmp/analysis-api-test` com tsc, depois `node --test tests/analysis-api.test.cjs`; executar também lint e build. Os resultados efetivamente obtidos nesta entrega estão no relatório de validação ao final do plano.

Documentação primária consultada: [QASM2/Qiskit](https://quantum.cloud.ibm.com/docs/en/api/qiskit/qasm2), [níveis de otimização](https://quantum.cloud.ibm.com/docs/en/qiskit/set-optimization). As assinaturas reais de Qiskit 2.6.0 e os resultados Docker governam a implementação.

## Resultado da entrega (16/11/2026)

157 testes Python aprovados, 1 teste optativo de RAG ao vivo omitido; 4 testes frontend aprovados; lint, TypeScript e build de produção aprovados. Foram executados 28 casos HTTP na versão final dos contêineres (14 na API e 14 pelo frontend), com os status esperados. Entradas de 2.000 linhas/20 qubits levaram aproximadamente 1,5–1,6 s nesses exemplos; entradas de 5.000 operações também passaram. Os serviços API e frontend estão saudáveis. Os cinco avisos da suíte são de depreciação de dependências.

Relatórios: [API](../artifacts/quantum-capacity-api.json), [proxy](../artifacts/quantum-capacity-proxy.json). Evidências e decisões completas: [plano concluído](superpowers/plans/2026-11-16-quantum-capacity.md).

Arquivos alterados/adicionados:

- Backend: `main.py`, `quantum/api.py`, `quantum/qiskit_service.py`, `quantum/worker.py`; novos `quantum/config.py`, `quantum/errors.py`, `quantum/http.py`, `quantum/python_parser.py`.
- Frontend: `frontend/services/analysis-api.ts`, `frontend/components/analysis/AnalysisForm.tsx`, `ResultsView.tsx`, `SourceDrawer.tsx`, `frontend/next.config.ts` e `frontend/Dockerfile`.
- Configuração/contrato: `.env.example`, `compose.yaml`, `Dockerfile`, `openapi.json`.
- Testes: `tests/test_circuit_capacity.py`, `tests/test_llm_qiskit.py`, `tests/test_qiskit_upstream.py`, `tests/test_api_original.py`, `frontend/tests/analysis-api.test.cjs`; script `scripts/verificar_circuitos_http.py`.
- Documentação/evidências: `README.md`, este guia, plano e dois relatórios JSON em `artifacts/`.
