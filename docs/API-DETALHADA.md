> **Atualização Beta-v2.0:** novas rotas `POST /qiskit/diagnose` e campos `perfil`, `prioridade` e `hardware` em `POST /qiskit/optimize`; `/query` ganhou `answer_mode`, `interpretation` e `evidence`. O contrato completo está em `openapi.json` e em [MOTOR-QUANTICO.md](MOTOR-QUANTICO.md). Obsoleto desde a Beta-v2.0: busca web.

# API GrowUp QuantumOne — guia detalhado

Este guia descreve os contratos implementados em `main.py`, `contratos.py`, `servicos.py`, `quantum/api.py` e `quantum/`. É voltado a quem precisa explicar, integrar ou depurar a API.

## 1. O que é a API

A API é o backend HTTP do QuantumOne. Ela recebe JSON ou arquivos multipart, valida o contrato, chama os serviços internos e devolve respostas JSON. FastAPI publica a documentação OpenAPI; Pydantic valida e serializa os modelos.

Há dois fluxos principais:

- **Circuitos:** análise determinística com Qiskit; não chama LLM, RAG ou Chroma.
- **Conhecimento:** busca semântica no Chroma e, nas rotas de consulta/conversa, geração de texto pela fila de LLMs NVIDIA (ou resposta extrativa local).

O frontend não chama o Chroma, Redis ou a LLM diretamente. Ele chama a API pela reescrita Next `/api/*`. A chave da NVIDIA existe somente no ambiente do serviço API.

## 2. Como acessar e explorar

Na instalação local com Docker:

- Swagger interativo: `http://localhost:8000/docs`
- Esquema OpenAPI: `http://localhost:8000/openapi.json`
- Saúde da API: `http://localhost:8000/health`
- Pelo frontend/proxy: prefixo `http://localhost:3000/api`, por exemplo `/api/qiskit/limits`.

No Swagger, selecione a rota, clique em **Try it out**, forneça o corpo e execute. O Swagger ajuda a testar a API; ele não substitui a leitura dos limites, dependências e efeitos de cada endpoint.

## 3. Contratos compartilhados

### Validação Pydantic

Os modelos comuns herdam de `Modelo`, configurado com `extra='forbid'` e remoção de espaços externos em strings. Isso significa que campos desconhecidos são rejeitados em vez de ignorados. Alguns campos numéricos também são estritos: por exemplo, booleano não é aceito como inteiro para `top_k` ou `score`.

### Identificador de solicitação

O middleware `identificar_chamada` cria um UUID em `request.state.request_id` antes de chamar a rota e o devolve no cabeçalho `X-Request-ID`. As respostas normais que usam modelo também incluem `request_id` no JSON. Ele permite que a equipe compare a mensagem do navegador com os logs sem registrar necessariamente o conteúdo completo enviado.

### Envelope de erro comum

Rotas gerais normalmente respondem com:

```json
{
  "request_id": "uuid-da-solicitacao",
  "code": "LLM_NOT_CONFIGURED",
  "message": "Descrição compreensível do erro.",
  "fields": []
}
```

`fields` aponta campos rejeitados quando a entrada não atende ao contrato. Erros do otimizador têm um envelope adicional descrito adiante. Para integrações, use o status HTTP e o código estável; não tente interpretar frases de mensagem como códigos.

### Limites de tempo

`API_TIMEOUT_SECONDS` define o timeout aplicado pelas rotas gerais que usam a função `chamar`. O endpoint de otimização tem seu próprio `OPTIMIZATION_TIMEOUT_SECONDS`, que também inclui criar/iniciar o worker. Um prazo do browser ou proxy é outro limite: cada camada precisa dar tempo para a camada abaixo responder.

## 4. Índice das rotas

| Método | Caminho | O que faz |
|---|---|---|
| GET | `/health` | Confirma que o processo FastAPI responde e informa o modo. |
| POST | `/query` | Busca contexto na base local e pede a resposta à fila de LLMs (ou responde de forma extrativa). Sem busca web. |
| POST | `/search` | Busca trechos no Chroma sem pedir geração à LLM. |
| POST | `/documents` | Valida e indexa um documento de texto enviado como multipart. |
| GET | `/documents` | Lista documentos presentes na coleção. |
| POST | `/evaluate` | Registra avaliação humana de uma resposta/caso. |
| GET | `/qiskit/limits` | Informa os limites efetivos do otimizador. |
| POST | `/qiskit/optimize` | Analisa e transpila QASM2 ou Python/Qiskit restrito. |
| POST | `/conversations` | Cria sessão de conversa persistente. |
| GET | `/conversations/{conversation_id}` | Recupera mensagens já registradas. |
| POST | `/conversations/{conversation_id}/messages` | Executa um turno, incluindo contexto conversacional, recuperação e LLM. |

## 5. Rotas gerais

### `GET /health`

**Finalidade:** liveness básico: confirma que a aplicação responde.

**Resposta 200:**

```json
{"status":"ok","mode":"integrated"}
```

O endpoint **não** executa uma transação de teste no Google, Chroma ou Redis. Um `200` demonstra que o processo HTTP está respondendo; não garante que todos os provedores externos estejam disponíveis.

### `POST /query`

**Finalidade:** responder uma pergunta usando recuperação documental local e a fila de LLMs NVIDIA (ver [LLM.md](LLM.md)); sem chave, ou se nenhum modelo responder a tempo, a resposta é extrativa local. Não há busca web.

**Cabeçalho:** `Content-Type: application/json`.

**Corpo:**

```json
{
  "input": "Como medir qubits em Qiskit?",
  "top_k": 5,
  "web_search": false
}
```

| Campo | Tipo | Regra |
|---|---|---|
| `input` | string | Obrigatório, não vazio, até 30.000 caracteres. |
| `top_k` | inteiro | Opcional, padrão 3, intervalo 1–10. |
| `web_search` | boolean ou `null` | **Obsoleto.** Aceito por compatibilidade e sempre ignorado: a busca web foi removida. `true` gera um aviso na resposta. |

**Resposta 200** (forma resumida; fontes e alertas variam):

```json
{
  "response": "A medição associa qubits a bits clássicos. [Fonte 1]",
  "sources": [
    {
      "document_id": "ibm_measure_qubits",
      "chunk_id": "identificador-do-trecho",
      "source": "https://quantum.cloud.ibm.com/docs/en/guides/measure-qubits",
      "content": "Conteúdo recuperado...",
      "origin": "local",
      "title": "Medir qubits",
      "retrieved_at": null
    }
  ],
  "status": "ok",
  "web_status": "disabled",
  "warnings": [],
  "search_suggestions_html": null,
  "request_id": "uuid",
  "mode": "integrated"
}
```

`status` é `ok` ou `insufficient_context`. `web_status` é `disabled`, `used` ou `not_used`. Em fontes locais, `origin` é `local`; resultados grounding de web incluem origem `web`, título e data. O conteúdo de fonte web contém segmentos da resposta ligados pelo grounding, não uma transcrição literal da página. `search_suggestions_html` pode trazer sugestões do provedor para exibição isolada; não deve ser tratado como código confiável.

**Erros importantes:**

- `503 LLM_NOT_CONFIGURED`: `NVIDIA_API_KEY` ausente **e** `QONE_LOCAL_FALLBACK=0`; use `/search` para recuperação sem geração.
- `503 WEB_SEARCH_UNAVAILABLE`: **removido**. Sem LLM, ou se nenhum modelo da fila responder, a resposta é extrativa local (`answer_mode: "local"`); `503 LLM_NOT_CONFIGURED` só ocorre com `QONE_LOCAL_FALLBACK=0`.
- `503 SERVICE_ERROR`: dependência do serviço indisponível.
- `504 TIMEOUT`: serviço externo excedeu o timeout geral.
- `422 INVALID_INPUT`: corpo não atende ao contrato.

Uma resposta gerada depende de um serviço externo: disponibilidade, cota, modelo e política do Google não são controlados pelo projeto. A API não deve fabricar resposta em caso de falha.

### `POST /search`

**Finalidade:** busca semântica na base local, sem chamar LLM para compor resposta.

**Corpo:**

```json
{"query":"transpiler stages","top_k":5}
```

`query` é obrigatório, de 1 a 2.000 caracteres. `top_k` vai de 1 a 10 e o padrão é 3.

**Resposta 200:**

```json
{
  "chunks": [
    {
      "document_id":"ibm_etapas_transpilacao",
      "chunk_id":"identificador-do-trecho",
      "source":"https://quantum.cloud.ibm.com/docs/en/guides/transpiler-stages",
      "content":"Trecho recuperado..."
    }
  ],
  "citations":["https://quantum.cloud.ibm.com/docs/en/guides/transpiler-stages"],
  "status":"ok",
  "request_id":"uuid",
  "mode":"integrated"
}
```

Se nenhum trecho for recuperado, `status` pode ser `insufficient_context`; isso não é uma falha HTTP. Os trechos são evidência disponível, não garantia de resposta completa.

### `POST /documents`

**Finalidade:** enviar um arquivo textual para indexação na mesma coleção vetorial consultada pela busca.

**Cabeçalho:** `Content-Type: multipart/form-data`.

**Campo do formulário:** `file`.

**Restrições:** extensão `.txt`, `.md`, `.py` ou `.qasm`; até 256 KiB; UTF-8; conteúdo precisa ser não vazio e não conter byte NUL. Nome de caminho enviado pelo cliente é reduzido ao nome de arquivo.

Exemplo curl:

```bash
curl -X POST http://localhost:8000/documents \
  -F "file=@exemplos/documento.txt"
```

**Resposta 201:**

```json
{"id":"upload_hash...","filename":"documento.txt","status":"sucesso","mode":"integrated"}
```

O ID é derivado de nome e conteúdo. Assim, repetir o mesmo upload é idempotente: evita a criação duplicada. Arquivos não são armazenados como um sistema de arquivos de downloads; o conteúdo é dividido/indexado no Chroma, com metadados para listar o documento.

**Erros:** `413` arquivo acima de 256 KiB; `415` extensão não permitida; `422` arquivo vazio, malformado, fora de UTF-8 ou impossível de dividir dentro do limite de tokens; `503` serviço de indexação indisponível.

### `GET /documents`

**Finalidade:** lista documentos únicos encontrados na coleção.

**Resposta 200:**

```json
{"documents":[{"id":"upload_hash...","filename":"documento.txt"}],"mode":"integrated"}
```

Essa lista é calculada a partir dos metadados do Chroma. Não representa um endpoint de leitura/baixar do conteúdo original.

### `POST /evaluate`

**Finalidade:** persistir uma avaliação **humana**; não chama modelo e não determina automaticamente se a resposta da IA está correta.

**Corpo:**

```json
{
  "case_id":"caso-001",
  "evaluator":"mentor",
  "ratings":[
    {"criterion":"pertinencia","score":4,"evidence":"Responde à pergunta apresentada."},
    {"criterion":"coerencia","score":5,"evidence":"A explicação mantém sequência lógica."},
    {"criterion":"aderencia","score":4,"evidence":"As afirmações correspondem ao circuito."},
    {"criterion":"clareza","score":4,"evidence":"A resposta é compreensível."},
    {"criterion":"falhas","score":5,"evidence":"Não observei erro factual neste caso."}
  ],
  "critical_failure":false,
  "critical_evidence":null
}
```

São necessários exatamente cinco critérios sem repetição: `pertinencia`, `coerencia`, `aderencia`, `clareza`, `falhas`. Cada nota é inteiro de 0 a 5 e traz evidência de 5–1.000 caracteres. `case_id` tem 1–100 caracteres; `evaluator`, 2–100. Se `critical_failure` for `true`, `critical_evidence` é obrigatório.

**Resposta 201:** repete a avaliação e acrescenta `id`, `created_at`, `rubric_version`, `mean`, `approved` e `result`. A média é a soma das cinco notas dividida por 5. `approved` exige média ≥ 4 e ausência de falha crítica. Nota zero em `coerencia`, `aderencia` ou `falhas` aciona revisão obrigatória mesmo se o campo crítico não foi marcado. Registro vai para JSON Lines em `AVALIACOES_PATH` (no Compose `/app/data/avaliacoes.jsonl`).

## 6. API de otimização Qiskit

### `GET /qiskit/limits`

Retorna os limites efetivos depois de ler as variáveis de ambiente. É útil para interface e cliente conhecerem a configuração deste deployment, em vez de duplicar constantes locais.

```json
{
  "max_circuit_lines":2500,
  "max_qubits":32,
  "max_classical_bits":256,
  "max_circuit_gates":10000,
  "max_circuit_depth":10000,
  "max_code_size_bytes":524288,
  "optimization_timeout_seconds":60.0,
  "max_concurrent_optimizations":1,
  "worker_memory_mb":1536,
  "max_python_steps":200000,
  "max_equivalence_qubits":8,
  "max_equivalence_operations":1000,
  "max_request_bytes":3162112
}
```

Os valores podem mudar conforme `.env`; a resposta acima ilustra defaults comuns. O orçamento do JSON é calculado a partir do limite de código e comporta o custo do escape. Consulte a resposta real do endpoint para integrar corretamente.

### `POST /qiskit/optimize`

**Finalidade:** ler, validar e transcompilar um circuito usando Qiskit, sem LLM.

**Cabeçalho:** `Content-Type: application/json`.

**Contrato atual:** informe exatamente um entre `qasm` ou `code`.

```json
{
  "code":"OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\nh q[0];",
  "format":"qasm",
  "optimization_level":3
}
```

| Campo | Regra |
|---|---|
| `qasm` | Campo legado para QASM2. Exclusivo com `code`; `format:"python"` não é compatível com `qasm`. |
| `code` | Código QASM2 ou subconjunto Python/Qiskit. Exclusivo com `qasm`. |
| `format` | `auto`, `qasm` ou `python`; padrão `auto`. Ao usar `qasm`, recomenda-se declarar `qasm` ou `code` com formato explicitamente. |
| `optimization_level` | Inteiro estrito de 0 a 3; padrão 3. Booleanos e valores fora da faixa são rejeitados. |

`format:"auto"` detecta o formato pelo conteúdo. Campos extras são rejeitados. Código vazio, corpo malformado, conteúdo que exceda bytes/linhas e limites estruturais têm erros tipados.

#### Resposta de sucesso

```json
{
  "original": {
    "qubits":2,
    "bits_classicos":0,
    "total_operacoes":1,
    "profundidade":1,
    "tipos_operacoes":{"h":1}
  },
  "proposta": {
    "qubits":2,
    "bits_classicos":0,
    "total_operacoes":1,
    "profundidade":1,
    "tipos_operacoes":{"u3":1}
  },
  "equivalente":true,
  "status_verificacao":"equivalente",
  "motivo":null,
  "diferenca_operacoes":0,
  "diferenca_profundidade":0,
  "reduziu_operacoes":false,
  "reduziu_profundidade":false,
  "circuito_otimizado_qasm":"OPENQASM 2.0;...",
  "formato_detectado":"qasm",
  "formato_saida":"qasm",
  "entrada_sha256":"sha256...",
  "tempo_processamento_segundos":0.08,
  "nivel_otimizacao":3,
  "passes_executados":[],
  "otimizacoes_aplicadas":[],
  "warnings":[]
}
```

Os campos exatos de métricas incluem, conforme resultado, qubits, bits clássicos, total de operações, profundidade, instruções, operações de dois qubits e contagem por tipo. O corpo de resposta inclui:

- `original` e `proposta`: métricas calculadas antes/depois da transpilação.
- `equivalente`: booleano quando verificado; `null` quando a verificação não coube/ não se aplicou.
- `status_verificacao` e `motivo`: resultado e justificativa da comparação.
- `diferenca_operacoes`, `diferenca_profundidade` e indicadores `reduziu_*`.
- `circuito_otimizado_qasm`: proposta exportada em QASM2 quando representável.
- `formato_detectado`, `formato_saida`, hash SHA-256 da entrada UTF-8 e duração do worker.
- `nivel_otimizacao`, `passes_executados`, `otimizacoes_aplicadas`, `warnings`.

A saída não é necessariamente menor. Transpilar significa reescrever/decompor para o conjunto de portas, e pode aumentar número de operações. Passes executados não garantem redução. `equivalente:null` significa que não houve comprovação, não que a proposta esteja comprovadamente diferente. Matriz de operador fica limitada por qubits/operações e não se aplica diretamente a medições e alguns tipos de controle clássico.

#### Subconjunto Python aceito

O endpoint **não** executa `exec`, `eval` nem o script do usuário. Uma AST é interpretada por regras explícitas. O escopo atual inclui construção de `QuantumCircuit`, registradores, operações aritméticas numéricas controladas, constantes/funções Math selecionadas, listas/tuplas, índices/fatias e `range`/`for` limitados, além de métodos de porta permitidos.

Imports arbitrários, arquivo, rede, `print`, classes, funções, comprehensions, while, atributos livres, parâmetros simbólicos `Parameter` e métodos fora da lista falham com `UNSUPPORTED_PYTHON`. Uma construção pode ser Python e Qiskit válido em geral e ainda não pertencer ao subconjunto seguro desta API. O erro informa linha e motivo.

#### Erros do otimizador

O envelope de circuito é mais detalhado que o envelope geral:

```json
{
  "request_id":"uuid",
  "code":"INVALID_CIRCUIT",
  "message":"Confira os campos e tipos da requisição.",
  "fields":[],
  "error":{
    "code":"INVALID_CIRCUIT",
    "message":"OpenQASM 2 inválido.",
    "details":{"line":46,"reason":"q[12] não existe..."}
  }
}
```

| HTTP / código | Causa provável | Próxima ação |
|---|---|---|
| 400 `INVALID_JSON` | Corpo JSON malformado | Corrigir JSON/escape de aspas e barras. |
| 415 `CONTENT_TYPE` | Não enviou `application/json` | Ajustar cabeçalho. |
| 422 `INVALID_REQUEST` | Campos, formato, tipo ou contrato inválido | Inspecionar `error.details.fields` e `issues`. |
| 422 `INVALID_CIRCUIT` | QASM/Python inválido segundo o parser/Qiskit | Corrigir primeira linha apontada e tentar novamente. |
| 422 `UNSUPPORTED_PYTHON` | Construção não existe no subconjunto do interpretador | Reescrever como construção permitida ou usar QASM válido. |
| 422 `UNSUPPORTED_OPERATION` | Porta opaca sem definição que permita otimização | Fornecer definição de porta ou usar porta suportada. |
| 413 `CIRCUIT_LIMIT_EXCEEDED` | Linhas, bytes, qubits, bits, operações ou profundidade ultrapassados | Consultar `/qiskit/limits`; simplificar ou ajustar deployment. |
| 413 `REQUEST_TOO_LARGE` | Corpo total JSON acima do orçamento ASGI | Reduzir a requisição. |
| 413 `OUTPUT_LIMIT_EXCEEDED` | JSON de resposta acima do limite | Reduzir/ajustar tamanho de circuito ou orçamento aprovado. |
| 429 `OPTIMIZER_BUSY` | Limite de otimização simultânea atingido | Repetir depois, com atraso. Não há fila ilimitada. |
| 499 `CLIENT_DISCONNECTED` | Cliente fechou a conexão | A API cancela e recolhe worker. 499 é código usado internamente e pode não ser tratado por todo intermediário. |
| 500 `OPTIMIZATION_FAILED` | Falha interna inesperada no worker | Usar `request_id`, conferir logs e preservar entrada para reprodução. |
| 502 `INVALID_WORKER_RESPONSE` | Processo terminou com resposta incompatível | Verificar versão/build e logs da API. |
| 503 `WORKER_RESOURCE_ERROR` | Processo, memória ou recursos insuficientes | Reduzir circuito ou revisar limites/recursos. |
| 504 `OPTIMIZATION_TIMEOUT` | Prazo do Qiskit expirou | Simplificar entrada ou ajustar timeout consciente dos recursos. |

### Como o worker é isolado

1. FastAPI valida formato e limites simples.
2. Lock/counter limita otimizações simultâneas.
3. API chama `python -m quantum.worker` como subprocesso e passa JSON pelo stdin.
4. Worker recebe ambiente reduzido; não herda `NVIDIA_API_KEY`.
5. Em Linux, `resource` aplica limites de endereço de memória, CPU, core dump, tamanho de arquivo e descritores.
6. O worker importa Qiskit, processa, serializa JSON e termina.
7. Timeout, desconexão do cliente ou término do worker cancela, mata quando preciso e coleta o subprocesso.

Os `RLIMIT_*` são específicos de Linux; as garantias de recurso valem para a execução Docker/Linux prevista. A segurança do código Python recebido vem principalmente do interpretador AST restrito, não da suposição de que um subprocesso comum seja uma sandbox para código arbitrário.

## 7. Conversas

As rotas de conversa usam os contratos de `/query` e SQLite para persistir mensagens.

### `POST /conversations`

Cria um UUID e inicializa um histórico vazio.

```json
{"conversation_id":"550e8400-e29b-41d4-a716-446655440000"}
```

### `GET /conversations/{conversation_id}`

Lê a conversa existente. UUID malformado é rejeitado; conversa inexistente retorna 404.

```json
{"conversation_id":"550e8400-e29b-41d4-a716-446655440000","messages":[]}
```

### `POST /conversations/{conversation_id}/messages`

Acrescenta turno. Corpo:

```json
{
  "input":"O que faz a porta CX?",
  "message_id":"f9f4f2a0-6cd3-4ebd-9cc9-43d40a5e4c96",
  "top_k":5,
  "web_search":false
}
```

`message_id` identifica idempotentemente o turno. Se houver retry de rede com o mesmo ID e mesmo conteúdo, a API retorna a resposta persistida sem pagar por nova chamada ao provedor. Reutilizar o mesmo ID para conteúdo diferente dá 409. Limites de conversa são 40 mensagens/turnos e 120.000 caracteres; ao excedê-los, a API pede para iniciar conversa nova (413). O histórico incluído na pergunta da LLM conserva a primeira interação e até seis turnos recentes.

Gravação SQLite usa versão otimista: se dois turnos concorrentes modificarem a mesma conversa, um deles pode receber 409 e deve ser revisto/repetido conforme a interface. A tabela SQLite é armazenada em `CONVERSAS_PATH` (default `data/conversas.sqlite3`); confirme que esse caminho está no volume desejado no deployment.

## 8. Formato de erros gerais

Os handlers de `main.py` mapeiam exceções para erros públicos que evitam vazar detalhes internos/segredos:

| HTTP | Código comum | Exemplo |
|---|---|---|
| 400 | `HTTP_ERROR` / `INVALID_JSON` no circuito | corpo malformado. |
| 404 | `HTTP_ERROR` | rota/conversa não encontrada. |
| 405 | `HTTP_ERROR` | método não aceito. |
| 413 | `HTTP_ERROR` ou `INVALID_INPUT` | arquivo ou conversa excedeu limite. |
| 415 | `CONTENT_TYPE` | mídia de entrada errada. |
| 422 | `INVALID_INPUT`, `INVALID_DOCUMENT` | contrato ou conteúdo de documento inválido. |
| 500 | `INTERNAL_ERROR` | falha interna sanitizada. |
| 502 | `INVALID_SERVICE_RESPONSE` | dado recebido de dependência fora do contrato. |
| 503 | `SERVICE_ERROR`, `LLM_NOT_CONFIGURED`, `WEB_SEARCH_UNAVAILABLE` | provedor ausente/indisponível. |
| 504 | `TIMEOUT` | chamada externa excedeu prazo. |

O status e código específico variam conforme a rota. Clientes devem esperar respostas com erro também em status não-2xx e fazer parse cuidadoso: o endpoint Qiskit tem `error.details`, enquanto várias rotas gerais retornam `fields` no envelope principal.

## 9. Fluxo de implementação por camada

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Roteamento geral | `main.py` | Rotas HTTP, middleware, identificador, timeout e mapeamento de erros. |
| Tipos | `contratos.py` | Schemas de requisição/resposta compartilhados. |
| Serviços da aplicação | `servicos.py` | Chamada assíncrona/threadpool para busca, RAG e documentos. |
| Rotas Qiskit | `quantum/api.py` | Contrato do circuito, worker, limite concorrente e timeout. |
| Domínio Qiskit | `quantum/qiskit_service.py` | Parsing, validação, transpilation, métricas e equivalência. |
| Python seguro | `quantum/python_parser.py` | Interpretação permitida de AST. |
| Worker | `quantum/worker.py` | Recursos do SO e fronteira de processo. |
| RAG e ingestão | `rag/` | Embeddings, Chroma, contexto, fila de LLMs, documentos e conversas. |

## 10. Como depurar uma chamada

1. Guarde o `request_id` mostrado ao usuário ou o cabeçalho `X-Request-ID`.
2. Confirme rota, método, `Content-Type` e corpo esperado.
3. Para Qiskit, consulte `/qiskit/limits` e confira primeiro `error.code`, depois linha/detalhes.
4. Para `/query`, confira `answer_mode` e `llm.tentativas` (qual modelo respondeu ou estourou o prazo); teste `/search` para separar recuperação local da geração.
5. Veja logs do container: `docker compose logs --since=10m api`.
6. Não copie API keys ou conteúdos privados para ticket/log público. Os códigos de solicitação permitem correlação sem precisar publicar o circuito inteiro.

Para reproduzir uma otimização sem consumir cota LLM:

```bash
curl -sS http://localhost:8000/qiskit/limits
curl -sS http://localhost:8000/qiskit/optimize \
  -H 'Content-Type: application/json' \
  -d '{"code":"OPENQASM 2.0; include \"qelib1.inc\"; qreg q[2]; h q[0];","format":"qasm"}'
```

## 11. Perguntas prováveis em uma banca/mentoria

**A API é REST?**  
É uma API HTTP organizada por recursos e operações, usando métodos GET/POST, status HTTP, JSON e OpenAPI. Algumas rotas de processamento são ações (`/qiskit/optimize`), portanto é mais preciso chamá-la de API HTTP REST-like em vez de afirmar aderência estrita a toda restrição REST.

**Por que separar `/search` e `/query`?**  
`/search` recupera evidência sem LLM; `/query` acrescenta geração da resposta. Essa separação permite testar/debugar recuperação e ainda consultar a base quando a LLM está indisponível.

**Por que `/health` não testa a LLM e o Chroma?**  
É uma verificação rápida de liveness. Uma checagem profunda chamaria dependências e poderia ficar lenta ou gerar tráfego/custos. Para diagnóstico, faça verificações específicas e observe status/logs das dependências.

**O que significa `top_k`?**  
Quantos resultados/candidatos a camada de busca deve recuperar, limitado a 1–10. Não é limite de tokens nem promessa de usar todos os trechos na resposta.

**O que significa `web_search:null`?**  
Nada: o campo é obsoleto e sempre ignorado, porque a busca web foi removida. `true` só gera um aviso na resposta.

**O que garante `equivalente:true`?**  
Que a comparação implementada concluiu equivalência dentro do orçamento/casos aplicáveis. `null` declara explicitamente que não houve prova. Nenhum resultado prova propriedades para um circuito fora do domínio/método de comparação usado.

**A API repete automaticamente uma otimização se a conexão cair?**  
Não se deve repetir cegamente o POST: a conexão pode cair depois de o worker concluir. A conversa usa `message_id` idempotente; o otimizador de circuito não tem chave idempotente equivalente no contrato atual.

**Por que o upload é limitado a 256 KiB?**  
O endpoint é para ingestão simples de texto e limita o corpo para controlar memória e custo de tokenização. O tamanho de upload é distinto dos limites de circuito e de trechos por documento.

**A coleção de documentos e o cache são iguais?**  
Não. Chroma armazena chunks e metadados persistentes. Redis armazena embeddings temporários com TTL; perder Redis causa recálculo, não perda de documentos.

**Quais itens ainda precisam de atenção antes de produção pública?**  
Autenticação/autorização, HTTPS e políticas de CORS/rede, política de retenção de histórico e avaliações, monitoramento, cotas de chamada Google e revisão de privacidade dos dados enviados ao provedor. O Compose atual é para execução local/restrita, não inclui autenticação de usuário.

## 12. Fontes no próprio repositório

- `main.py`: middlewares, rotas gerais, conversas e handlers.
- `contratos.py`: modelos Pydantic.
- `servicos.py`: adaptador para Chroma/RAG.
- `quantum/api.py`, `quantum/config.py`, `quantum/http.py`, `quantum/worker.py`: contrato, limites e isolamento de circuito.
- `docs/circuitos-grandes.md`: limites e subconjunto Python em maior detalhe.
- `openapi.json`: especificação OpenAPI versionada; a aplicação em execução é fonte de verdade para contrato atual.
- `http://localhost:8000/docs`: testar a instância local.
