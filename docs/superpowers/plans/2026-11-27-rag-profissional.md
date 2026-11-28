# RAG profissional do QuantumOne — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevar o RAG (recuperação, consulta, contexto e verificação) medindo cada camada contra um benchmark ampliado.

**Architecture:** Camadas opcionais ligadas por `RAG_<NOME>` (módulo `rag/recursos.py`) sobre o pipeline atual (`busca_chromadb` → `contexto` → `cliente_llm`/`resposta_local`). Cada camada tem fallback para o comportamento anterior e só vira padrão se passar no portão de métricas do design.

**Tech Stack:** Python 3.12, sentence-transformers (embeddings e cross-encoder), ChromaDB via HTTP, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-rag-profissional-design.md`

## Global Constraints

- Modelos somados ≤ ~600 MB; nenhum download sem autorização explícita do dono (`GROWUP_PERMITIR_DOWNLOAD=1` só depois de perguntar).
- Sem busca web; ChromaDB e provedor NVIDIA mantidos; NVIDIA é opcional: sem chave ou com falha, caminho 100% local.
- Retrieval p95 ≤ 600 ms em CPU, sem LLM.
- Fora do domínio recusado em 100% (hoje 1,000) em dev e reservado.
- Modelo novo = coleção nova; a coleção atual nunca é alterada nem apagada.
- Branch `feat/rag-profissional`; nada de commit com `git add -A` (o working tree tem mudanças alheias): sempre caminhos explícitos.
- Python: venv com Python 3.12 (`%LOCALAPPDATA%\Programs\Python\Python312\python.exe`); não tocar na stack `growup-integrada`.
- Portão de cada camada (tarefas 3–7): melhora Recall@5/MRR/nDCG@5 ou fidelidade no conjunto `dev`, sem piorar o `reservado`, a recusa fora do domínio nem o orçamento de latência. Caso contrário, a camada fica desligada por padrão.

## Review Focus

- Pergunta vazia, de 1 palavra ou só com bloco de código colado: sem exceção, sem chamada de LLM, resultado coerente (testes nas tarefas 5 e 7).
- Modelo (embedding ou reranker) ausente localmente: fallback silencioso para o comportamento anterior, com aviso no traço, nunca erro 500 (tarefas 3 e 4).
- Resposta da LLM de reescrita malformada, vazia ou com timeout: usa só a consulta original (tarefa 5).
- Duas coleções (antiga e nova) coexistindo: busca nunca mistura vetores de modelos diferentes (tarefa 3).
- Chamadas concorrentes na primeira carga do reranker/modelo: um único carregamento (tarefa 4).

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `rag/recursos.py` (novo) | `ativo(nome) -> bool`: lê `RAG_<NOME>` (`0/1`), com padrões definidos na tarefa 8 |
| `rag/suporte.py` (novo) | `pontuar_suporte(frase, fontes, embed=None) -> float` em [0,1]; usado pela métrica e pela verificação |
| `rag/reranker.py` (novo) | cross-encoder preguiçoso e thread-safe |
| `rag/consulta.py` (novo) | reescrita, multi-query e decomposição |
| `rag/diversidade.py` (novo) | `mmr` puro |
| `rag/verificacao.py` (novo) | verificação frase a frase da resposta |
| `rag/configuracao.py`, `banco.py`, `preparar_chunks.py`, `indexar_documentos.py` | modelo, prefixos, limite de tokens, coleção versionada |
| `rag/busca_chromadb.py`, `contexto.py`, `pipeline.py` | integração das camadas e traço |
| `rag/avaliacao_rag.py`, `tests/rag_benchmark.json` | benchmark ampliado, divisão, fidelidade, comparação por camada |
| `tests/test_rag_*.py` (novos) | testes por tarefa |

---

### Task 1: Branch, linha de base e benchmark ampliado

**Files:**
- Modify: `tests/rag_benchmark.json`, `rag/avaliacao_rag.py`
- Create: `tests/test_rag_benchmark_schema.py`, `docs/benchmarks/rag-linha-de-base-2026-11-27.json`

**Interfaces:**
- Produces: cada caso do gabarito ganha `"split": "dev" | "reservado"` e `"idioma": "pt" | "en"`; CLI `python -m rag.avaliacao_rag --saida X --split dev|reservado|todos`; `avaliar_modo(modo, casos, k)` e `resumir(linhas, k)` mantêm as assinaturas.

- [ ] **Step 1:** `git checkout -b feat/rag-profissional` (carrega o working tree atual).
- [ ] **Step 2: Write failing test** em `tests/test_rag_benchmark_schema.py`: carrega o gabarito e afirma `len(casos) >= 140`; todo caso tem `id` único, `split` ∈ {dev, reservado}, `idioma` ∈ {pt, en}; reservado ≥ 30% e ≤ 40% dos casos; ≥ 12 casos fora do domínio (sem `relevantes`) distribuídos nos dois splits; todo `relevantes` existe como `document_id` em `artifacts/conhecimento` ou no conhecimento interno; os 53 ids antigos continuam presentes.
- [ ] **Step 3:** Rodar: espera FAIL (ainda 53 casos).
- [ ] **Step 4:** Rodar a avaliação atual uma vez (`--split todos`, modelo atual) e salvar em `docs/benchmarks/rag-linha-de-base-2026-11-27.json`; conferir que bate com `docs/RAG.md` (Recall@5 0,827, MRR 0,911 no gabarito antigo).
- [ ] **Step 5:** Ampliar `tests/rag_benchmark.json`: ~100 casos novos escritos a partir dos documentos do corpus: paraphrases PT/EN, perguntas curtas/ambíguas, identificadores de código, perguntas comparativas/compostas (≥ 15), foco extra em `quantum_info` e `medicao`; reservado escolhido por sorteio com semente fixa e gravado no arquivo. Casos antigos recebem `split`/`idioma`. (Tarefa delegável a um agente, se o dono autorizar.)
- [ ] **Step 6:** `avaliacao_rag.main`: adicionar `--split` (filtra `casos`) e gravar `split` no relatório.
- [ ] **Step 7:** Rodar o teste de esquema: PASS. Commit: `git add tests/rag_benchmark.json tests/test_rag_benchmark_schema.py rag/avaliacao_rag.py docs/benchmarks/` → `test: amplia benchmark do RAG com divisão dev/reservado`.

### Task 2: Flags de camada, suporte e métrica de fidelidade

**Files:**
- Create: `rag/recursos.py`, `rag/suporte.py`, `tests/test_rag_suporte.py`
- Modify: `rag/avaliacao_rag.py`

**Interfaces:**
- Produces: `recursos.ativo(nome: str) -> bool` (env `RAG_<NOME em maiúsculas>`, `"1"` liga; padrão `False` até a tarefa 8); `suporte.pontuar_suporte(frase: str, fontes: list[str], embed: Callable[[list[str]], list[list[float]]] | None = None) -> float` (máximo entre sobreposição lexical ponderada por IDF de `tokens` e similaridade de cosseno por embedding quando `embed` é dado; resultado em [0,1]); em `resumir`: chave `"fidelidade_resposta_local"` (média de suporte das frases da resposta local, esperada ~1,0) e `"fidelidade_llm"` (só com respostas fornecidas, senão `None`).

- [ ] **Step 1: Write failing tests** em `tests/test_rag_suporte.py`: (a) frase copiada literalmente de uma fonte → `>= 0.9`; (b) frase sobre tema diferente → `<= 0.3`; (c) paráfrase com sinônimos do glossário PT→EN, sem `embed` → maior que a do item (b); (d) `fontes=[]` → `0.0`; (e) `ativo("reranker")` é `False` sem env, `True` com `RAG_RERANKER=1` (monkeypatch).
- [ ] **Step 2:** Rodar: FAIL (módulos inexistentes).
- [ ] **Step 3:** Implementar `rag/recursos.py` e `rag/suporte.py`, reutilizando `texto.tokens` e `glossario.expandir`.
- [ ] **Step 4:** Em `avaliacao_rag.py`, adicionar as métricas ao `resumir` e `--respostas ARQUIVO.json` opcional (lista `{id, resposta, fontes}`).
- [ ] **Step 5:** Rodar os testes: PASS. Commit: `git add rag/recursos.py rag/suporte.py rag/avaliacao_rag.py tests/test_rag_suporte.py` → `feat: pontuação de suporte e métrica de fidelidade do RAG`.

### Task 3: Embeddings, prefixos, chunking e coleção versionada

**Files:**
- Modify: `rag/configuracao.py`, `rag/banco.py`, `rag/preparar_chunks.py`, `rag/indexar_documentos.py`, `rag/busca_chromadb.py:130`, `rag/avaliacao_rag.py` (`preparar` e `main`)
- Create: `scripts/reindexar_rag.py`, `tests/test_rag_embeddings.py`

**Interfaces:**
- Produces em `configuracao`: `MODELO_EMBEDDINGS` lê `RAG_EMBEDDING_MODEL` (fallback `EMBEDDING_MODEL`, padrão atual inalterado); `prefixo(tipo: Literal["consulta","passagem"], modelo: str = MODELO_EMBEDDINGS) -> str` (`"query: "`/`"passage: "` para nomes contendo `e5`, `""` caso contrário); `limite_tokens(modelo=None) -> int` = `min(RAG_CHUNK_TOKENS (padrão 110), max_seq_length)`; `estrategia_chunks(limite: int) -> str` (`"secoes_tokens_v2"` se 110, senão `f"secoes_tokens_v2_{limite}"`); `nome_colecao(modelo, limite) -> str` (`CHROMA_COLLECTION` se definido; nome atual `qiskit_documentacao_multilingue` se modelo e limite são os legados; senão `qone_rag_<slug do modelo>_<limite>`); `criar_embeddings(tipo="consulta")` aplica o prefixo.
- `gerar_chunks(documento, tokenizer, limite, nome_modelo, estrategia=None)` grava `estrategia` (padrão `"secoes_tokens_v2"`); `validar_lote` aceita `tokens <= limite_tokens()` em vez de 110 fixo; `banco.obter_colecao` espera nome e `estrategia_chunks` vindos de `configuracao`.

- [ ] **Step 1: Write failing tests** em `tests/test_rag_embeddings.py`: `prefixo("consulta", "intfloat/multilingual-e5-base") == "query: "`, `prefixo("passagem", "<modelo legado>") == ""`; `nome_colecao` legado igual ao nome atual e diferente para e5; `limite_tokens` respeita `RAG_CHUNK_TOKENS=256` com modelo fake de `max_seq_length=512` e é limitado por `max_seq_length=128`; `validar_lote` aceita chunk de 200 tokens com limite 256 e rejeita 300; coleção existente com metadados de outro modelo levanta `ColecaoIncompativel` (a busca nunca mistura vetores).
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar as funções de `configuracao` e ajustar `banco`, `preparar_chunks`, `indexar_documentos`; `busca_chromadb` e `avaliacao_rag.preparar` passam a codificar consulta e passagens com os prefixos corretos.
- [ ] **Step 4:** Implementar `scripts/reindexar_rag.py`: lê `documentos/base_documental.json`, grava na coleção do modelo ativo (reaproveita `indexar_documentos.main`), imprime contagens, nunca toca em outra coleção.
- [ ] **Step 5:** Rodar `pytest tests/test_rag_embeddings.py tests/test_cliente_chroma.py tests/test_conhecimento_web.py tests/test_conhecimento_interno.py -v`: PASS (os testes antigos provam que o padrão legado não mudou). Commit com caminhos explícitos: `feat: embeddings configuráveis com prefixos e coleção versionada`.
- [ ] **Step 6 (portão, exige autorização do dono):** perguntar antes de baixar `intfloat/multilingual-e5-base` (e `-small`); conferir no Hugging Face id e tamanho reais. Com `GROWUP_PERMITIR_DOWNLOAD=1`, rodar `avaliacao_rag --split dev` e `--split reservado` para o modelo legado e para os candidatos (limites 110 e 256). Salvar relatórios em `docs/benchmarks/`; escolher a configuração pelo portão e registrar em `docs/RAG.md` (modelo, tamanho do chunk).

### Task 4: Reranker neural

**Files:**
- Create: `rag/reranker.py`, `tests/test_rag_reranker.py`
- Modify: `rag/busca_chromadb.py` (depois do RRF, antes do agrupamento por seção), `rag/avaliacao_rag.py` (modo `hibrido+reranker`)

**Interfaces:**
- Consumes: `recursos.ativo("reranker")`, `configuracao`.
- Produces: `reranker.reordenar(pergunta: str, candidatos: list[dict], topo: int = 30, pontuador=None) -> list[dict]` — cada candidato tem `{"id","texto","nota"}`; devolve a lista ordenada por `nota_reranker`, acrescentando essa chave; `pontuador` injeta um callable `(pares) -> list[float]` para testes; sem modelo disponível devolve a lista de entrada intacta e registra `log.warning`; `reranker.disponivel() -> bool`. Modelo via `RAG_RERANKER_MODEL`.

- [ ] **Step 1: Write failing tests**: com `pontuador` fake que dá nota alta ao candidato "B", `reordenar` coloca "B" primeiro e preserva todos os ids; com `pontuador=None` e modelo ausente (monkeypatch do carregador para lançar `OSError`) devolve a ordem original sem lançar; 8 threads chamando a carga simultaneamente chamam o carregador uma única vez (contador); lista vazia → lista vazia.
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar com `CrossEncoder` carregado sob `threading.Lock`, `local_files_only` seguindo `GROWUP_PERMITIR_DOWNLOAD`, `trust_remote_code=False`.
- [ ] **Step 4:** Integrar em `buscar_documentos`: quando ativo, aplicar nos 30 primeiros pós-RRF; a nota final combina `nota_reranker` normalizado com os multiplicadores existentes (título, identificador, biblioteca, obsoleto) — pesos calibrados pelo benchmark, não adivinhados.
- [ ] **Step 5:** Rodar os testes: PASS. Commit: `feat: reranker cross-encoder opcional na recuperação`.
- [ ] **Step 6 (portão, exige autorização de download):** comparar `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilíngue) e `cross-encoder/ms-marco-MiniLM-L-6-v2` (leve, inglês) — conferir ids e tamanhos antes de baixar; manter só se passar pelo portão e p95 ≤ 600 ms.

### Task 5: Entendimento da consulta

**Files:**
- Create: `rag/consulta.py`, `tests/test_rag_consulta.py`
- Modify: `rag/busca_chromadb.py` (separar a etapa de candidatos e aceitar várias consultas), `rag/pipeline.py`

**Interfaces:**
- Consumes: `cliente_llm.conversar(mensagens, modelos=None, tarefa="Resposta", ao_progresso=None, transporte=None, temperatura=0.2, max_tokens=None, ao_parcial=None) -> dict`, `cliente_llm.fila_rapida()`, `cliente_llm.configurada()`, `cliente_llm.FilaEsgotada`, `glossario.expandir`.
- Produces: `consulta.expandir_consulta(pergunta: str, conversa: str | None = None, transporte=None) -> list[str]` — primeiro item é sempre a `pergunta` original; até 4 itens, sem duplicatas; usa a fila rápida com JSON `{"consultas": [...]}`; qualquer falha (sem chave, `FilaEsgotada`, JSON inválido, lista vazia) devolve `[pergunta]` mais uma variante determinística com termos do glossário quando houver; cache LRU por `(pergunta, conversa)`. `busca_chromadb.buscar_documentos(pergunta, ..., consultas: list[str] | None = None)` — com várias consultas, gera candidatos para cada uma e funde os rankings por RRF antes da reordenação.

- [ ] **Step 1: Write failing tests**: (a) transporte fake devolvendo `{"consultas":["a","b"]}` → `["pergunta","a","b"]`; (b) texto não JSON → `["pergunta", ...]` sem lançar; (c) sem `NVIDIA_API_KEY` → nenhuma chamada de rede (transporte que falha o teste se usado); (d) `FilaEsgotada` → fallback; (e) pergunta vazia e pergunta só com bloco de código → sem chamada de LLM e sem exceção; (f) resposta com 10 consultas → máximo 4; (g) busca com `consultas=["x","y"]` sobre `ColecaoMemoria` devolve a união fundida com o melhor rank por documento.
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar `consulta.py` (prompt curto em PT que pede reescrita autônoma usando a conversa, termos técnicos em inglês e decomposição de perguntas compostas; temperatura baixa).
- [ ] **Step 4:** Integrar em `pipeline.responder` atrás de `recursos.ativo("consulta")`: usa `pergunta` (que inclui histórico nas conversas) como `conversa` e `consulta` como pergunta; registra as variantes no traço.
- [ ] **Step 5:** Rodar `pytest tests/test_rag_consulta.py tests/test_fila_llm.py tests/test_sem_busca_web.py -v`: PASS. Commit: `feat: reescrita e multi-query de consultas com fallback local`.
- [ ] **Step 6 (portão):** o benchmark offline usa variantes gravadas (gerar uma vez com a API, só se o dono autorizar usar a chave, e salvar em `tests/fixtures/consultas_dev.json`); sem chave, medir apenas a variante determinística. Manter a camada só se passar pelo portão.

### Task 6: Contexto — MMR e orçamento por relevância

**Files:**
- Create: `rag/diversidade.py`, `tests/test_rag_contexto.py`
- Modify: `rag/contexto.py`, `rag/busca_chromadb.py` (incluir `embeddings` na consulta), `rag/avaliacao_rag.py` (`ColecaoMemoria.query` aceita `embeddings` em `include`)

**Interfaces:**
- Produces: `diversidade.mmr(itens: list[tuple[str, list[float], float]], lambda_: float = 0.7, k: int = 5) -> list[str]` — itens `(id, vetor, nota)`; devolve ids na ordem de escolha; `contexto.preparar_contexto(documentos, limite=12000)` mantém a assinatura e o retorno `(texto, fontes)`, mas divide o orçamento proporcionalmente à `pontuacao` quando há mais de um documento (o primeiro nunca recebe menos de 40% do limite).

- [ ] **Step 1: Write failing tests**: `mmr` com dois vetores quase idênticos e um distante, `k=2` → escolhe o melhor e o distante; `lambda_=1.0` equivale à ordenação por nota; `k` maior que o número de itens → devolve todos; três documentos de 5.000 caracteres com `limite=6000` → as três fontes aparecem em `fontes` e o texto total ≤ 6000; documento único mantém o comportamento atual (texto cortado ao limite); documento sem texto continua ignorado; `limite < 500` continua levantando `ValueError`.
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar `mmr` (numpy, cosseno) e a divisão de orçamento; integrar o MMR em `buscar_documentos` atrás de `recursos.ativo("mmr")`, aplicado aos candidatos antes do agrupamento por seção.
- [ ] **Step 4:** Rodar os testes: PASS. Commit: `feat: MMR e orçamento de contexto por relevância`.
- [ ] **Step 5 (portão):** benchmark com e sem MMR e com os dois orçamentos; manter só o que passa.

### Task 7: Verificação da resposta

**Files:**
- Create: `rag/verificacao.py`, `tests/test_rag_verificacao.py`
- Modify: `rag/pipeline.py`, `rag/avaliacao_rag.py`

**Interfaces:**
- Consumes: `suporte.pontuar_suporte`, `texto.frases`, `recursos.ativo("verificacao")`.
- Produces: `verificacao.verificar_resposta(resposta: str, fontes: list[dict], embed=None, limiar: float | None = None) -> dict` com `{"frases": [{"texto","fontes","suporte","suportada"}], "fidelidade": float, "texto": str}`; `fontes` são os dicts do pipeline (`content`; `[Fonte N]` = posição 1-based); citação inexistente (`[Fonte 9]`) → `suportada=False`; blocos de código e frases de cabeçalho/limitação não são avaliados; `limiar` padrão vem de `RAG_LIMIAR_SUPORTE`. No `pipeline.responder`, quando ativo e `answer_mode == "llm"`: frases não suportadas viram aviso em `warnings`; se `fidelidade < RAG_MIN_FIDELIDADE`, descarta a resposta da LLM e usa `resposta_local.gerar`, com aviso explícito.

- [ ] **Step 1: Write failing tests**: frases literais das fontes → `fidelidade == 1.0`; uma frase inventada entre duas verdadeiras → `suportada=False` só nela e `fidelidade ≈ 0.67`; citação `[Fonte 9]` inexistente → não suportada; só bloco de código → `fidelidade == 1.0` sem lançar; resposta vazia → `fidelidade == 0.0`; no pipeline (monkeypatch de `cliente_llm.gerar_resposta` devolvendo frase inventada, `RAG_MIN_FIDELIDADE=0.8`) → `answer_mode == "local"` e aviso presente; com a camada desligada o texto da LLM passa intacto.
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar `verificar_resposta`.
- [ ] **Step 4:** Calibrar `RAG_LIMIAR_SUPORTE` no `dev`: positivos = frases da resposta local; negativos = as mesmas frases com a fonte trocada por outra de tema diferente; escolher o limiar que maximiza F1 e conferir no `reservado`. Registrar em `docs/RAG.md`. (Limitação declarada: fidelidade de respostas livres da LLM só é medida com `--respostas`.)
- [ ] **Step 5:** Rodar os testes e `tests/test_integracao.py`: PASS. Commit: `feat: verificação frase a frase das respostas do RAG`.

### Task 8: Traço, padrões finais, documentação e fechamento

**Files:**
- Modify: `rag/pipeline.py`, `rag/recursos.py` (padrões), `.env.example`, `openapi.json`, `docs/RAG.md`, `docs/BENCHMARKS.md`
- Create: `tests/test_rag_traco.py`

**Interfaces:**
- Produces: campo `trace` na resposta de `responder`: `{"etapas": {nome: segundos}, "consultas": [...], "candidatos": int, "camadas": {nome: bool}, "modelo_embeddings": str, "fallbacks": [str]}`.

- [ ] **Step 1: Write failing tests** em `tests/test_rag_traco.py`: `responder` com coleção em memória devolve `trace` com as chaves acima e `camadas` refletindo `RAG_*`; fallback do reranker ausente aparece em `fallbacks`; `status == "insufficient_context"` também traz `trace`; o schema em `openapi.json` contém `trace` (comparar com a resposta de `/query`).
- [ ] **Step 2:** Rodar: FAIL.
- [ ] **Step 3:** Implementar o traço (tempos já existem em `buscar_documentos(tempos=...)`); atualizar `openapi.json` pelo mesmo gerador usado pelo projeto (conferir em `main.py` e `scripts/`).
- [ ] **Step 4:** Rodar a avaliação final `--split dev` e `--split reservado` com a configuração candidata a padrão e salvar em `docs/benchmarks/rag-avaliacao.json`. Ajustar os padrões de `recursos.py` e `.env.example` para as camadas que passaram no portão; documentar em `docs/RAG.md` (tabelas antes/depois, limitações, como religar/desligar cada camada) e `docs/BENCHMARKS.md`; conferir `recall_por_tema` ≥ 0,70 em `quantum_info` e `medicao`.
- [ ] **Step 5:** Suíte completa: `pytest tests -q --ignore=tests/test_live.py` (o live exige serviços): tudo PASS; conferir p95 e recusa fora do domínio.
- [ ] **Step 6:** Commit com caminhos explícitos: `feat: traço do RAG, padrões calibrados e documentação`. Revisão final com o agente `ecc:rag-pipeline-reviewer` somente se o dono autorizar.

---

## Self-review

- **Cobertura do design:** etapas 0 e 1 → Task 1; fidelidade → Task 2; embeddings/chunking/coleção versionada → Task 3; reranker → Task 4; consulta → Task 5; MMR/contexto → Task 6; verificação → Task 7; observabilidade/docs/openapi → Task 8. "Temas fracos ≥ 0,70" é checado na Task 8, Step 4.
- **Consistência de tipos:** `recursos.ativo`, `pontuar_suporte`, `reordenar`, `expandir_consulta`, `mmr`, `verificar_resposta` aparecem com a mesma assinatura onde são consumidos.
- **Limitações assumidas:** ids e tamanhos dos modelos candidatos (e5, cross-encoders) não foram verificados online; os passos de download pedem conferência e autorização antes. A fidelidade de respostas livres da LLM só é medida com respostas gravadas.
