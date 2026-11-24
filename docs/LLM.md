# Modelos de linguagem: fila NVIDIA sequencial

O QuantumOne usa os modelos da NVIDIA (API compatível com OpenAI em `integrate.api.nvidia.com`) em
quatro tarefas da análise unificada (`POST /analise`, ver [ARQUITETURA.md](ARQUITETURA.md)):

| Tarefa | Fila | O que o modelo recebe |
|---|---|---|
| Planejar a busca | rápida | pedido resumido → JSON com consultas para a base e para a internet |
| Corrigir código quebrado | principal | código + diagnóstico do Qiskit + trechos da base sobre os erros |
| Redigir a resposta | principal | pedido, arquivos, resultados do Qiskit, base local [Fonte N] e internet [Web N] |
| `/query` (rota antiga) | principal | pergunta + trechos da base |

Toda correção proposta por um modelo é **revalidada pelo Qiskit** (worker isolado) antes de ser
aceita; se o carregador recusar, o motivo vai para `correcao.falhas` e o modelo tenta mais uma vez.
A integração com o Gemini foi removida em 23/11/2026 (código, dependência `google-genai` e variáveis `GEMINI_*`).

## Como a fila funciona (`rag/cliente_llm.py`)

- **Um modelo de cada vez.** O primeiro recebe a requisição; se não terminar em
  `LLM_TIMEOUT_SECONDS` (padrão **30 s**, qualquer que seja o tamanho do prompt), a conexão é fechada
  (a requisição é cancelada) e só então o segundo é chamado, com mais 30 s, e assim por diante. Nunca há
  dois modelos esperando juntos, nem entre tarefas: a busca na base e na internet, que corre em paralelo
  às correções, não usa LLM.
- **Sem teto total** (`LLM_TEMPO_TOTAL_SECONDS=0`): a fila percorre os 7 modelos se preciso
  (pior caso ≈ 7 × 30 s). O teto da análise inteira é `QONE_ANALISE_TIMEOUT_SECONDS` (900 s); passada a
  metade dele, os circuitos restantes ficam só com a correção automática.
- **Streaming** (`LLM_STREAMING=1`): a resposta chega em pedaços. O prazo de 30 s vale para a resposta
  inteira; se o modelo estourar no meio, o texto parcial é descartado e o próximo modelo começa do zero
  (o texto de um modelo nunca aparece misturado ao do seguinte).
- **Progresso ao vivo:** antes de cada tentativa, a fila avisa qual modelo está sendo consultado
  (“Redigindo a resposta: consultando nvidia/nemotron-3-ultra-550b-a55b (1/7)”). A interface lê isso em
  `GET /analise/progresso/{id}` a cada 0,5 s e mostra o modelo em uso, o texto que ele já escreveu
  (“Escrevendo agora”), o tempo na etapa e o histórico.
- **Pausas só para erros permanentes:** 401/403/404 pausam o modelo por 1 h (chave ou modelo não
  liberado) e 429 por 60 s. Tempo esgotado ou resposta vazia **não** pausam: na próxima pergunta o
  modelo é tentado de novo.
- **Sem limite de tokens prático:** `LLM_MAX_TOKENS=16384` (aceito pelos 7 modelos).
- **Raciocínio desligado:** nos modelos Nemotron, o corpo leva
  `chat_template_kwargs: {"enable_thinking": false}`, para que respondam direto em vez de gastar o
  prazo pensando. `LLM_RACIOCINIO=1` religa. Blocos `<think>` que ainda venham são removidos.
- A resposta informa o modelo usado e cada tentativa (`llm.modelo`, `llm.tentativas`), e a interface
  mostra “Redigida por <modelo>”.

### Fila principal (7 modelos, ordem de prioridade)

| # | Modelo |
|---|---|
| 1 | `nvidia/nemotron-3-ultra-550b-a55b` |
| 2 | `nvidia/nemotron-3.5-super-vl-120b-a12b` |
| 3 | `nvidia/nemotron-3.5-lightning-30b-a3b` |
| 4 | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` |
| 5 | `poolside/laguna-xs-2.1` |
| 6 | `nvidia/nemotron-3.5-bolt-e4b` |
| 7 | `nvidia/ising-calibration-1.5-31b` |

`NVIDIA_MODELS` (nomes separados por vírgula) troca a ordem ou a lista.

### Fila rápida (planejamento)

Lightning → Bolt → Laguna → Super. O planejamento só precisa de um JSON curto, então começa pelos
modelos mais rápidos. `NVIDIA_MODELS_RAPIDOS` troca a lista.

### Medições de 23/11/2026 (stack Docker, chave gratuita)

Com 30 s por modelo, streaming, busca em paralelo, correção automática sem LLM quando basta e respostas
mais enxutas:

| Envio | Antes (15 s, tudo em sequência) | Agora | O que aconteceu agora |
|---|---|---|---|
| Pasta com Python legado (GHZ com `Aer`, `execute`, `cnot`) + README | 43–72 s | **14,8 s** | correção automática completa (LLM dispensada); base e internet em paralelo; Ultra redigiu em 8,1 s |
| QASM quebrado na mensagem (`cnot`, índice fora do registrador) | 27–55 s | **19,8 s** | Ultra corrigiu e o Qiskit validou; Ultra redigiu em streaming |
| Só texto (pergunta sobre transpilação), medição anterior | 46,7 s | não remedido | antes: Ultra estourou 15 s e o Super redigiu |

Streaming com prompt curto (uma chamada por modelo): o primeiro pedaço de texto chega em 0,6–0,9 s
nos seis primeiros modelos e em 3 s no Ising. Nas medições acima, o Ultra respondeu dentro dos 30 s;
se estourar, o Super assume com mais 30 s.

## Ordem de decisão

1. Fora do domínio (sem termos quânticos, código ou arquivos): nenhuma LLM é chamada (`insufficient_context`).
2. `QONE_LLM=local`: resposta extrativa local, offline.
3. Com `NVIDIA_API_KEY`: fila NVIDIA.
4. Sem chave, ou se os 7 modelos falharem: resposta extrativa local (`QONE_LOCAL_FALLBACK=1`, padrão),
   com os resultados do Qiskit preservados.

## Limites do serviço

O nível gratuito da NVIDIA tem limite de requisições por minuto (o valor real aparece em
build.nvidia.com); um 429 pausa só o modelo afetado por 60 s. O uso exige internet e envia o pedido,
os trechos e o código à NVIDIA. Para nada sair da máquina, use `QONE_LLM=local` e
`QONE_PESQUISA_WEB=0`.
