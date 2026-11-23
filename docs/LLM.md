# Geração de respostas: fila de LLMs

O QuantumOne só usa um modelo de linguagem para **redigir** a resposta a partir dos trechos da base
local. Nenhum provedor recebe ferramenta de busca, e todos recebem apenas a pergunta e os trechos.

## Ordem de decisão (`rag/pipeline_gemini.py`)

1. Sem evidência suficiente na base: nenhuma LLM é chamada (`insufficient_context`).
2. `QONE_LLM=local`: resposta extrativa local, offline.
3. Com `NVIDIA_API_KEY`: **fila de prioridade NVIDIA** (`rag/cliente_llm.py`).
4. Sem chave NVIDIA e com `GEMINI_API_KEY`: Gemini.
5. Se a fila (ou o Gemini) falhar: resposta extrativa local (`QONE_LOCAL_FALLBACK=1`, padrão).

## Fila NVIDIA

Cada modelo tem até `LLM_TIMEOUT_SECONDS` (padrão **15 s**). Se não responder a tempo, responder
vazio ou devolver erro, a fila passa ao próximo. Quem falha fica pausado por `LLM_PAUSA_SEGUNDOS`
(300 s; 404 = modelo não liberado para a conta, pausa 12× maior), para que as próximas perguntas não
esperem por ele. `LLM_TEMPO_TOTAL_SECONDS` (45 s) limita a fila inteira. Blocos `<think>` são
removidos. A resposta informa o modelo usado e cada tentativa (`llm.modelo`, `llm.tentativas`), e a
interface mostra "Redigida por <modelo>".

### Ordem padrão (7 modelos)

| # | Modelo | Medição de 23/11/2026 (prompt real do RAG, 3 chamadas, limite de 10 s) |
|---|---|---|
| 1 | `nvidia/nemotron-3-ultra-550b-a55b` | 3/3 dentro de 10 s (5,8–8,9 s), correto e com citações |
| 2 | `nvidia/nemotron-3.5-super-vl-120b-a12b` | 1/3 dentro de 10 s (7,9 s), correto e com citações |
| 3 | `nvidia/nemotron-3.5-lightning-30b-a3b` | 1/3 dentro de 10 s (6,8 s), correto e com citações |
| 4 | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | 3/3 (3,2–6,4 s), correto e com citações |
| 5 | `poolside/laguna-xs-2.1` | 3/3 (1,2–2,5 s), correto e com citações |
| 6 | `nvidia/nemotron-3.5-bolt-e4b` | 3/3 (1,8–2,1 s), correto e com citações |
| 7 | `nvidia/ising-calibration-1.5-31b` | 3/3 (2,0–3,2 s), correto, **sem** citações |

A ordem segue a capacidade estimada (tamanho e geração do modelo): o Super (120B) e o Lightning
(geração 3.5) ficam acima do Nano-Omni (geração 3). Em 23/11/2026, a pedido do projeto, o limite
passou para **15 s** e o Super e o Lightning entraram na fila; eles **não foram remedidos** com 15 s.
Com contexto grande, os três primeiros podem estourar o prazo; o disjuntor os pausa por 5 minutos.

Com 15 s por modelo e teto de 45 s (`LLM_TEMPO_TOTAL_SECONDS`, abaixo dos 60 s de `API_TIMEOUT_SECONDS`),
no pior caso cabem três tentativas por pergunta; se as três estourarem, a resposta é local, e os
modelos pausados são pulados nas perguntas seguintes.

Ficaram de fora: Mistral Large 4, Kimi K3.1 Pro, Step 5, MiniMax M3.1, GLM 5.3, Gemma 4 31B e Muse
Glimmer (0/3, todos acima de 10 s); StarCoder2, Cosmos Reason2 e Gemma 3 (HTTP 404 para esta conta).

Para trocar a fila, defina `NVIDIA_MODELS` com nomes separados por vírgula, na ordem desejada.

### Limites do serviço
O nível gratuito da NVIDIA tem limite de requisições (cerca de 40 por minuto, segundo fontes de
terceiros; o valor real aparece em build.nvidia.com). Ele exige internet e envia a pergunta e os
trechos à NVIDIA. Para nada sair da máquina, use `QONE_LLM=local`.
