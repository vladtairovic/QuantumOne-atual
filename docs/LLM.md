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

Cada modelo tem até `LLM_TIMEOUT_SECONDS` (padrão **10 s**). Se não responder a tempo, responder
vazio ou devolver erro, a fila passa ao próximo. Quem falha fica pausado por `LLM_PAUSA_SEGUNDOS`
(300 s; 404 = modelo não liberado para a conta, pausa 12× maior), para que as próximas perguntas não
esperem por ele. `LLM_TEMPO_TOTAL_SECONDS` (45 s) limita a fila inteira. Blocos `<think>` são
removidos. A resposta informa o modelo usado e cada tentativa (`llm.modelo`, `llm.tentativas`), e a
interface mostra "Redigida por <modelo>".

### Ordem padrão (5 modelos)

Medida em 23/11/2026 com um prompt real do RAG (~6.000 caracteres de trechos + pergunta sobre migração
de `qc.cnot`), 3 chamadas por modelo e limite de 10 s, com a chave do projeto:

| # | Modelo | Dentro de 10 s | Tempo | Correta | Cita fontes |
|---|---|---|---|---|---|
| 1 | `nvidia/nemotron-3-ultra-550b-a55b` | 3/3 | 5,8–8,9 s | 3/3 | 3/3 |
| 2 | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | 3/3 | 3,2–6,4 s | 3/3 | 3/3 |
| 3 | `poolside/laguna-xs-2.1` | 3/3 | 1,2–2,5 s | 3/3 | 3/3 |
| 4 | `nvidia/nemotron-3.5-bolt-e4b` | 3/3 | 1,8–2,1 s | 3/3 | 3/3 |
| 5 | `nvidia/ising-calibration-1.5-31b` | 3/3 | 2,0–3,2 s | 3/3 | 0/3 |

A ordem segue a capacidade do modelo (tamanho/família e qualidade observada) entre os que cumpriram
o limite. O `ising-calibration` ficou por último porque não segue a regra de citar `[Fonte N]`.

Ficaram de fora: `nemotron-3.5-super-vl-120b` e `nemotron-3.5-lightning-30b` (1/3 dentro de 10 s);
Mistral Large 4, Kimi K3.1 Pro, Step 5, MiniMax M3.1, GLM 5.3, Gemma 4 31B e Muse Glimmer (0/3, todos
acima de 10 s); StarCoder2, Cosmos Reason2 e Gemma 3 (HTTP 404 para esta conta). A disponibilidade e a
latência mudam com o tempo; refaça a medição antes de alterar a ordem.

Para trocar a fila, defina `NVIDIA_MODELS` com nomes separados por vírgula, na ordem desejada.

### Limites do serviço
O nível gratuito da NVIDIA tem limite de requisições (cerca de 40 por minuto, segundo fontes de
terceiros; o valor real aparece em build.nvidia.com). Ele exige internet e envia a pergunta e os
trechos à NVIDIA. Para nada sair da máquina, use `QONE_LLM=local`.
