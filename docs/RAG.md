# RAG do QuantumOne

## Pipeline

1. **Interpretação** (`rag/interpretacao.py`): tipo de entrada, bibliotecas, intenção (`conceito`,
   `depuracao`, `otimizacao`, `implementacao`, `comparacao`, `conversao`, `simulacao`), complexidade e
   hipóteses explícitas quando a pergunta é ambígua ou curta.
2. **Recuperação híbrida** (`rag/busca_chromadb.py`):
   - busca vetorial no ChromaDB (modelo multilíngue `paraphrase-multilingual-MiniLM-L12-v2`);
   - BM25 em memória sobre os mesmos trechos, com tokenização de identificadores de código e
     expansão PT→EN (`rag/glossario.py`; termos expandidos pesam 0,6);
   - fusão por Reciprocal Rank Fusion (k=60);
   - reordenação: identificador citado literalmente (×1,25), título/seção que nomeia o conceito
     (×1,2), biblioteca detectada (×1,15), fonte marcada como desatualizada (×0,5, exceto em
     perguntas de migração);
   - agrupamento por seção, na ordem original dos trechos.
3. **Verificação de evidência** (`avaliar_evidencia`): fração dos termos da pergunta cobertos pelos
   trechos (direto ou pelo glossário). Abaixo de `RAG_MIN_COBERTURA` (0,34) **nenhuma resposta é
   gerada** e o status é `insufficient_context`.
4. **Resposta**:
   - com `NVIDIA_API_KEY`: a fila de LLMs ([LLM.md](LLM.md)) recebe só a pergunta e até 6.000 caracteres de trechos locais (sem ferramenta de
     busca), com instruções para separar fonte de inferência e alertar sobre APIs antigas;
   - sem chave, com `QONE_LLM=local` ou se nenhum modelo responder a tempo: **resposta extrativa local**
     (`rag/resposta_local.py`), feita de frases copiadas literalmente das fontes, com `[Fonte N]`.

## Redução de alucinações

- Nada é gerado sem evidência mínima; perguntas fora do domínio são recusadas.
- A resposta local não sintetiza: a literalidade medida é 100%.
- As instruções enviadas à LLM proíbem inventar APIs, versões, fontes e execuções, e exige marcar inferências.
- Conteúdo com APIs removidas do Qiskit recebe `desatualizado=true` e gera aviso na resposta.
- O conhecimento interno é identificado como tal (`qone://conhecimento/...`), nunca como documentação oficial.

## Avaliação objetiva

`python -m rag.avaliacao_rag --saida docs/benchmarks/rag-avaliacao.json` executa o mesmo código de
produção sobre um corpus em memória (snapshots de `artifacts/conhecimento` + conhecimento interno;
32 documentos, 5.286 trechos) com o modelo real, usando o gabarito `tests/rag_benchmark.json`
(47 perguntas respondíveis em 18 temas + 6 fora do domínio). K = 5.

| Métrica | Linha de base (vetorial pura) | Vetorial + reordenação | Lexical (BM25) | **Híbrido (padrão)** |
|---|---|---|---|---|
| Recall@5 | 0,671 | 0,716 | 0,817 | **0,827** |
| Precision@5 | 0,302 | 0,323 | 0,383 | **0,387** |
| MRR | 0,750 | 0,819 | 0,876 | **0,911** |
| nDCG@5 | 0,625 | 0,685 | 0,786 | **0,808** |
| Acerto no 1º lugar | 0,660 | 0,745 | 0,809 | **0,851** |
| Precisão das citações (resposta local) | 0,583 | 0,632 | 0,684 | **0,699** |
| Literalidade da resposta local | 1,000 | 1,000 | 1,000 | 1,000 |
| Respondíveis marcadas sem evidência | 0,149 | 0,128 | 0,000 | 0,021 |
| Fora do domínio recusadas | 1,000 | 1,000 | 1,000 | 1,000 |
| Latência média / p95 (CPU, ms) | 103 / 119 | 112 / 136 | 55 / 82 | 104 / 115 |

Ajustes calibrados no gabarito, e só mantidos porque melhoraram as métricas: reforço de título
(1,0 → 1,2: MRR 0,886 → 0,911) e corte da resposta local (0,5 → 0,7: precisão das citações
0,630 → 0,699). Valores maiores foram testados e descartados para não superajustar um gabarito pequeno.

### Limitações da avaliação
- O gabarito foi escrito pela mesma equipe que escreveu o conhecimento interno; ele mede regressões,
  não substitui avaliação humana independente.
- O corpus avaliado é o snapshot local (IBM + conhecimento interno). As 308 fontes verificadas do
  catálogo só entram na base após a sincronização administrativa.
- Fidelidade das respostas redigidas pela LLM não é medida automaticamente (exigiria a API); a métrica de
  fidelidade vale para a resposta local.
- Não há reranker neural: a reordenação é por regras e foi medida.

## Diagnóstico do pipeline (o que funciona e o que não)

| Etapa | Estado | Evidência |
|---|---|---|
| Ingestão e chunking | funciona | testes de chunking, limite de tokens e idempotência |
| Embeddings e banco vetorial | funciona, mas fraco sozinho em perguntas PT sobre docs EN | Recall@5 0,671 na linha de base |
| Busca lexical | forte em termos técnicos e identificadores | Recall@5 0,817 |
| Fusão híbrida | melhor combinação | MRR 0,911 |
| Evidência insuficiente | recusa 100% das perguntas fora do domínio, 2% de falsos "sem evidência" | avaliação |
| Temas fracos | `quantum_info` (0,42) e `medicao` (0,50) de recall | `recall_por_tema` no relatório |

## Camadas opcionais (implementadas; desligadas por padrão no código, reranker ligado na implantação Docker)

Projeto e plano: [design](superpowers/specs/2026-11-27-rag-profissional-design.md) e
[plano](superpowers/plans/2026-11-27-rag-profissional.md). Cada camada liga com `RAG_<NOME>=1`
(`rag/recursos.py`; qualquer outro valor, ou variável ausente, significa desligada). **Nenhuma camada virou
padrão no código** (na implantação Docker o reranker vem ligado por decisão do dono, ver "Docker" abaixo): as camadas medidas não mostraram ganho (consulta, MMR, embeddings e5) ou deixam uma escolha ao dono
(reranker), e a verificação e o orçamento não foram medidos (ver "Medições com modelos reais"). O caminho padrão é o mesmo de antes, acrescido apenas do campo `trace`.

| Camada | Variável | O que faz | Parâmetros (padrão) | Se falhar |
|---|---|---|---|---|
| Reranker | `RAG_RERANKER=1` | Reordena os primeiros candidatos (`RAG_RERANKER_TOPO`, padrão 30, limitado a 5..100; reduzir troca revocação por latência, e o custo cresce linearmente) com um cross-encoder local e mistura a nota neural com a do RRF | `RAG_RERANKER_MODEL` (vazio), `RAG_RERANKER_PESO` (0,7), `RAG_RERANKER_TOPO` (30) | Sem modelo, modelo ausente no disco, erro de inferência ou notas inválidas: mantém a ordem anterior |
| Consulta | `RAG_CONSULTA=1` | A LLM reescreve a pergunta em variantes; as buscas são fundidas por RRF. Só age se houver LLM configurada | usa a fila rápida de modelos | Falha da LLM: variante local (glossário); erro inesperado: só a pergunta original |
| MMR | `RAG_MMR=1` | Diversifica a cabeça do ranking pela similaridade entre vetores (modos híbrido e vetorial) | `RAG_MMR_CANDIDATOS` (20), `RAG_MMR_LAMBDA` (0,7) | Qualquer erro: mantém a ordem anterior |
| Orçamento | `RAG_ORCAMENTO=1` | Quando o contexto precisa ser cortado, divide os caracteres entre as fontes proporcionalmente à relevância (em vez de preencher em sequência) | `GROWUP_MAX_CONTEXT_CHARS` | Sem corte necessário, o resultado é idêntico ao sequencial |
| Verificação | `RAG_VERIFICACAO=1` | Confere cada frase da resposta da LLM contra as fontes; avisa sobre frases sem suporte e descarta a resposta (usando a local) se a fidelidade ficar abaixo do mínimo | `RAG_LIMIAR_SUPORTE` (0,5), `RAG_MIN_FIDELIDADE` (0,6) | Erro na verificação: mantém a resposta da LLM e registra no `trace` |

Embeddings e chunking têm padrão legado (`paraphrase-multilingual-MiniLM-L12-v2`, 110 tokens). Trocar
`RAG_EMBEDDING_MODEL` ou `RAG_CHUNK_TOKENS` aponta para **outra coleção** do ChromaDB (a antiga não é
tocada e uma coleção incompatível é recusada); é preciso reindexar, e baixar um modelo novo exige
`GROWUP_PERMITIR_DOWNLOAD=1`. Para ligar ou desligar uma camada, defina ou remova a variável no ambiente
da API (veja `.env.example`) e reinicie; `trace.camadas` mostra o que estava ativo em cada resposta.

Com `RAG_CONSULTA=1` a resposta traz também `variantes_consulta`, e com `RAG_VERIFICACAO=1` traz
`verificacao` (`fidelidade` e `frases_sem_suporte`, ou `{"erro": true}`). `/query` também carrega esses dois
campos, `variantes_consulta` e `verificacao`, que são `null` enquanto as camadas estão desligadas.

A reescrita da consulta tem prazo próprio: `RAG_CONSULTA_TIMEOUT` (segundos, padrão 4, limitado a 0,5-30)
vale por modelo **e** como teto total da reescrita, na fila rápida; estourado, cai na variante local (glossário) e
registra o fallback no `trace`. `RAG_CHUNK_TOKENS` aceita só inteiros >= 16; valor inválido volta ao padrão (110) com um aviso.

### Docker: o que o repositório entrega (reranker, reescrita, orçamento e verificação LIGADOS; MMR desligado)

Decisão do dono (explícita): o reranker `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` fica **ligado por padrão na
implantação Docker**, e ele aceita ultrapassar o orçamento anterior de modelos (600 MB) e a meta de p95 de 600 ms
("não se prenda ao tamanho nem ao tempo"). Fora do Docker (`python -m ...`) o padrão do código continua sendo
reranker desligado; o que mudou é o `compose.yaml`.

O que o `compose.yaml` (serviço `api`) entrega:

- repasse das variáveis da camada RAG, com padrões que **não exigem mudança no `.env`**: `RAG_RERANKER=1`,
  `RAG_RERANKER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, `RAG_RERANKER_TOPO=30`, `RAG_RERANKER_PESO=0.7`;
  desde 28/11/2026 também ficam **ligadas** a reescrita, o orçamento de contexto e a verificação (decisão do
  dono): `RAG_CONSULTA=1`, `RAG_ORCAMENTO=1`, `RAG_VERIFICACAO=1`, `RAG_CONSULTA_TIMEOUT=4`. O **MMR fica
  desligado** (`RAG_MMR=0`): ligado junto com as outras, ele piorou a busca nas medições (seção "Camadas
  combinadas"), e o dono autorizou desligá-lo. Cada uma pode ser mudada pelo `.env` com `=0` ou `=1`. Na análise da interface (`/analise`) a verificação só informa a
  fidelidade (campo `verificacao`), sem descartar a resposta; em `/query` ela mantém a regra original e
  descarta respostas abaixo de `RAG_MIN_FIDELIDADE` (0,6);
- recursos: `API_MEMORY_LIMIT` padrão `4g` (antes 2g) e `API_CPU_LIMIT` padrão `4` (antes 2), porque o reranker
  e o worker do Qiskit (até 1536 MB) não devem caber com folga em 2 GB (estimativa por soma, hipótese não medida; a validação usou 4 GB); `OMP_NUM_THREADS=4` (o Dockerfile fixa 2, o
  ambiente do contêiner prevalece). `pids_limit`, `cap_drop` e `no-new-privileges` não mudaram;
- aquecimento na partida (parte da inicialização: a API só aceita requisições depois dele; numa primeira partida com
  o volume `model_cache` vazio e `GROWUP_PERMITIR_DOWNLOAD=1`, os cerca de 470 MB são baixados antes disso, e é o
  `start_period: 600s` do healthcheck do compose, com `interval: 30s` e `retries: 5`, que cobre essa espera; com o
  volume semeado a partida medida levou no máximo 35 s): `ServicosIntegrados.inicializar` chama `reranker.pre_carregar()` logo após o
  aquecimento do modelo de embeddings, antes de a API aceitar requisições. Ele carrega o cross-encoder e roda um
  par de teste; se falhar (modelo ausente, sem rede), registra um aviso, devolve `False` e a API sobe do mesmo
  jeito, com cada consulta caindo no fallback seguro (`trace.fallbacks` traz `reranker: modelo indisponível`).
  Não há nova tentativa de carga a cada consulta.
- `CHROMA_COLLECTION` e `EMBEDDING_MODEL` não mudaram: o reranker não toca na coleção, então não é preciso
  reindexar.

**Desligar ou trocar o modelo** (no ambiente/`.env` que o Compose lê):

```bash
RAG_RERANKER=0                                              # volta ao comportamento anterior (sem custo de latência)
RAG_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L6-v2      # modelo leve (88 MB), efeito pequeno
```

**Como o modelo chega ao contêiner.** O cache do Hugging Face fica no volume `model_cache`
(`/home/growup/.cache/huggingface`). Com `GROWUP_PERMITIR_DOWNLOAD=1` (padrão do compose), a primeira partida
baixa o reranker (cerca de 470 MB) para esse volume e as seguintes reutilizam. Com `GROWUP_PERMITIR_DOWNLOAD=0`
nada é baixado em execução e o modelo precisa estar no volume antes: foi o que se fez na validação isolada
(abaixo), copiando `models--cross-encoder--mmarco-mMiniLMv2-L12-H384-v1` e
`models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2` do cache do host para `<volume>/hub/` com um
contêiner descartável (`cp -r`, depois `chown -R 10001:10001`; o usuário `growup` do contêiner é o uid 10001).

**Validação isolada (28/11/2026, medida).** Projeto Compose `growup-rerank-teste` (porta 18081, volumes
próprios, só `api`, `cache` e `chroma`), `QONE_LLM=local` sem chave (respostas extrativas, sem chamadas
externas), `GROWUP_PERMITIR_DOWNLOAD=0` com o cache semeado, coleção com 371 trechos (base documental de 3
documentos + conhecimento interno). Não toquei na pilha `growup-integrada`.

- A API ficou saudável em no máximo 35 s (primeira partida: saudável na 7ª verificação de 5 s) e o log mostrou o reranker carregado e aquecido
  (2,1 s) antes de aceitar requisições, sem nenhum download.
- 35 perguntas reais do benchmark (29 do domínio, pt e en, e 6 fora do domínio): HTTP 200 em todas,
  `trace.camadas.reranker == true` e `trace.fallbacks == []` em todas, fontes não vazias em todas as 29 do domínio.
  `status`: 27 `ok` e 8 `insufficient_context` (as 6 fora do domínio e 2 do domínio, `n116` e `n123`, que ficaram abaixo de
  `RAG_MIN_COBERTURA`).
- Latência por requisição (cliente, 4 CPUs): mediana 0,88 s, p95 1,10 s, máximo 1,16 s; no `trace`, a etapa
  "Fusão RRF e reordenação" (onde roda o reranker) tem mediana de 0,85 s. Com `RAG_RERANKER=0`, nas mesmas
  35 perguntas: mediana 0,025 s e p95 0,047 s. Ou seja, o reranker custa cerca de 0,85 s por consulta nesta
  máquina e configuração: a meta de p95 de 600 ms **foi ultrapassada** (aceito pelo dono).
- Efeito na ordem: em 31 das 35 perguntas a lista de fontes (3 primeiras) difere entre ligado e desligado. Nesta
  coleção pequena, o documento relevante apareceu entre as 3 primeiras em 21 de 29 perguntas nos dois casos, e no
  1º lugar em 18 (ligado) contra 17 (desligado). **Não é uma medição de qualidade**: a coleção isolada não tem todos
  os documentos que o benchmark espera, e o ganho de MRR (0,805 para 0,885 no reservado) vem das medições locais
  acima, não desta.
- Recursos, com o reranker ligado: memória em repouso 1,375 GiB de 4 GiB; durante 10 consultas sequenciais, no máximo
  1,39 GiB amostrados (`docker stats`, uma amostra a cada cerca de 2 s) e CPU em torno de 360%; com 3 chamadas a
  `/qiskit/optimize` (circuito QASM de 20 qubits, 960 portas, nível 3, todas HTTP 200 em 1,5 a 2,1 s) em paralelo com
  10 consultas, no máximo 1,50 GiB amostrados. O pico do cgroup (`memory.peak`, inclui cache de páginas) foi 2,1 GB.
  `OOMKilled=false` e `RestartCount=0`. (O circuito era leve; **não** foi testado o worker no limite de 1536 MB.)
- Falha controlada: com `RAG_RERANKER_MODEL=cross-encoder/does-not-exist` e download desligado, a API subiu e ficou
  saudável, com um aviso no log; as 12 consultas devolveram HTTP 200, com `trace.fallbacks == ["reranker: modelo
  indisponível"]` e sem erro 500.
- Limitações: um único host (12 CPUs, 16 GB, Docker Desktop), execução serial, coleção pequena; a latência em outro
  hardware ou com a coleção completa pode ser diferente (hipótese, não medida).

**Se a coleção ou o modelo de embeddings mudar** (não faz parte desta entrega): `CHROMA_COLLECTION` explícito tem
prioridade sobre o nome derivado do modelo e do limite (`rag/configuracao.py`, `nome_colecao`); para uma coleção
nova, remova o `CHROMA_COLLECTION` fixo ou aponte-o para o nome novo e reindexe antes de trocar o tráfego.

## Novo benchmark (240 casos, divisão dev/reservado)

`tests/rag_benchmark.json` tem 240 casos (169 em português, 71 em inglês), incluindo os 53 antigos e casos
fora do domínio. Cada caso pertence a `dev` (154 casos: 142 respondíveis + 12 fora do domínio) ou a
`reservado` (86 casos: 80 respondíveis + 6 fora do domínio). O reservado serve só para conferir decisões
tomadas olhando o dev. Os relatórios são gerados offline com o modelo legado (K = 5):

```bash
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split dev --saida docs/benchmarks/rag-avaliacao-dev.json
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split reservado --saida docs/benchmarks/rag-avaliacao-reservado.json
```

Resultados medidos em 27/11/2026 (`docs/benchmarks/rag-avaliacao-dev.json` e `-reservado.json`). O
`rag-avaliacao.json` e a tabela da seção anterior descrevem o benchmark antigo de 53 casos e não foram alterados.

| Modo | Recall@5 dev / reservado | MRR dev / reservado | nDCG@5 dev / reservado | Fora do domínio recusadas dev / reservado |
|---|---|---|---|---|
| Linha de base (vetorial pura) | 0,767 / 0,744 | 0,729 / 0,711 | 0,697 / 0,658 | 1,000 / 1,000 |
| Vetorial + reordenação | 0,782 / 0,775 | 0,735 / 0,770 | 0,704 / 0,707 | 1,000 / 1,000 |
| Lexical (BM25) | 0,917 / 0,838 | 0,809 / 0,775 | 0,811 / 0,756 | 0,833 / 0,833 |
| **Híbrido (padrão atual)** | **0,919 / 0,852** | **0,833 / 0,805** | **0,828 / 0,765** | 0,833 / 0,833 |
| Híbrido + MMR | 0,881 / 0,817 | 0,806 / 0,803 | 0,797 / 0,747 | 1,000 / 1,000 |

Latência do híbrido: média 115 / 91 ms e p95 145 / 116 ms (CPU, dev / reservado; varia entre execuções).

`recall_por_tema` do híbrido nos dois temas que o plano mirava (meta: recall ≥ 0,70 em cada um):

| Tema | dev | reservado | Meta ≥ 0,70 |
|---|---|---|---|
| `quantum_info` | 0,964 | 0,854 | atingida nos dois conjuntos |
| `medicao` | 0,750 | 0,600 | atingida no dev, **não atingida no reservado** |

Leitura honesta:
- Estes números não são comparáveis com a tabela do benchmark antigo: o conjunto de casos e a mistura de
  temas mudaram. A queda do dev para o reservado (híbrido: Recall@5 0,919 → 0,852) é o motivo de existir o reservado.
- `quantum_info` e `medicao` estão acima dos 0,42 e 0,50 antigos, mas com outro conjunto de casos; não
  atribuímos essa diferença a nenhuma camada opcional (todas estavam desligadas) e não a medimos de forma isolada.
- **Fora do domínio:** o híbrido recusa 10 de 12 (dev) e 5 de 6 (reservado), isto é, 0,833, **abaixo** da meta
  de 100% do projeto. Só a linha de base, a vetorial com reordenação e o MMR chegam a 1,0; no MMR, a hipótese é que isso venha da
  mudança do top-k usado no cálculo de cobertura e não de um ganho de qualidade (hipótese, não isolada
  experimentalmente). O ponto segue em aberto.
- O gabarito foi escrito pela mesma equipe que escreveu o conhecimento interno; mede regressões, não
  substitui avaliação humana independente.

### Resultado do portão do MMR (medido)

No dev, híbrido → híbrido + MMR (λ = 0,7): Recall@5 0,919 → 0,881, MRR 0,833 → 0,806, nDCG@5 0,828 → 0,797.
No reservado: 0,852 → 0,817, 0,805 → 0,803, 0,765 → 0,747. O MMR piora a recuperação nos dois conjuntos
então **reprovou o portão e permanece desligado**. Quedas medidas de recall por tema no dev (híbrido → híbrido + MMR):
`fundamentos` 0,833 → 0,500, `circuitos_parametrizados` 1,000 → 0,767, `simulacao` 0,917 → 0,667,
`correcao_de_erros` 0,833 → 0,667 (e `primitivas` 1,000 → 0,833); `transpilacao` subiu 0,843 → 0,926.
A causa é uma hipótese, não isolada experimentalmente: documentos relevantes com vários trechos próximos
podem ser afastados pelo MMR. Valores de λ maiores (0,85 a 0,9) não foram testados.

### Verificação de respostas: calibração e limitação

`python -m rag.avaliacao_rag --calibrar-limiar` varre limiares de suporte com positivos (trechos
literais das fontes) e negativos (trechos de outro tema). Resultado: melhor limiar 0,70, F1 0,9988 no dev
(816 + 816 pares) e 0,9978 no reservado (460 + 460). **Limitação:** a tarefa é fácil (os positivos são
extratos literais), então a calibração só vale para respostas extrativas. Respostas livres da LLM provavelmente terão
notas menores (não medido) e precisam ser medidas com `--respostas` antes de qualquer padrão; por isso o limiar padrão no
código continua em 0,5 e a camada fica desligada. A verificação usa similaridade lexical com glossário (sem
embeddings). O orçamento proporcional de contexto também **não foi medido**: o benchmark mede recuperação,
não o contexto enviado à LLM.

### Medições com modelos reais (28/11/2026)

Depois da autorização do dono (download de modelos e uso da chave da NVIDIA), todas as configurações abaixo
foram medidas no mesmo gabarito de 240 casos (dev 154 / reservado 86), K = 5, em CPU local. Fonte de todos os
números: `docs/benchmarks/rag-medicoes-2026-11-28.json` (32 medições). Modelos baixados: `intfloat/multilingual-e5-small`
(471 MB), `cross-encoder/ms-marco-MiniLM-L6-v2` (88 MB) e `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (470 MB).
Cada célula é "dev / reservado". Todos os padrões continuam **desligados** (nada foi promovido).

**Qualidade da recuperação (modo híbrido):**

| Configuração | Recall@5 dev / res | MRR dev / res | nDCG@5 dev / res |
|---|---|---|---|
| Híbrido legado (estado atual) | 0,919 / 0,852 | 0,833 / 0,805 | 0,828 / 0,765 |
| Híbrido, e5-small, chunk 110 | 0,920 / 0,834 | 0,820 / 0,795 | 0,818 / 0,754 |
| Híbrido, e5-small, chunk 256 | 0,909 / 0,859 | 0,837 / 0,786 | 0,826 / 0,763 |
| Híbrido + reranker ms-marco (88 MB), topo 30 | 0,922 / 0,851 | 0,860 / 0,797 | 0,846 / 0,782 |
| Híbrido + reranker mmarco (470 MB), topo 30 | 0,933 / 0,886 | 0,921 / 0,885 | 0,889 / 0,843 |
| Híbrido + reranker mmarco, topo 20 | 0,920 / 0,878 | 0,907 / 0,868 | 0,875 / 0,828 |
| Híbrido + reranker mmarco, topo 15 | 0,915 / 0,862 | 0,903 / 0,868 | 0,871 / 0,820 |
| Híbrido + consulta local (glossário) | 0,903 / 0,869 | 0,808 / 0,802 | 0,806 / 0,773 |
| Híbrido + consulta (variantes da LLM) | 0,906 / 0,858 | 0,815 / 0,807 | 0,808 / 0,771 |
| Híbrido + MMR (λ 0,7) | 0,881 / 0,817 | 0,806 / 0,802 | 0,797 / 0,747 |

| Configuração | Acerto no 1º dev / res | Fora do domínio recusadas dev / res |
|---|---|---|
| Híbrido legado (estado atual) | 0,725 / 0,700 | 0,833 / 0,833 |
| Híbrido, e5-small, chunk 110 | 0,711 / 0,725 | 0,833 / 0,833 |
| Híbrido, e5-small, chunk 256 | 0,732 / 0,700 | 0,833 / 0,833 |
| Híbrido + reranker ms-marco (88 MB), topo 30 | 0,789 / 0,713 | 1,000 / 1,000 |
| Híbrido + reranker mmarco (470 MB), topo 30 | 0,866 / 0,825 | 0,917 / 1,000 |
| Híbrido + reranker mmarco, topo 20 | 0,852 / 0,800 | 0,917 / 1,000 |
| Híbrido + reranker mmarco, topo 15 | 0,845 / 0,800 | 0,917 / 1,000 |
| Híbrido + consulta local (glossário) | 0,697 / 0,688 | 0,833 / 0,833 |
| Híbrido + consulta (variantes da LLM) | 0,704 / 0,713 | 1,000 / 0,833 |
| Híbrido + MMR (λ 0,7) | 0,711 / 0,700 | 1,000 / 1,000 |

**Meta de recall ≥ 0,70 por tema:**

| Configuração | `quantum_info` dev / res | `medicao` dev / res |
|---|---|---|
| Híbrido legado (estado atual) | 0,964 / 0,854 | 0,750 / 0,600 |
| Híbrido, e5-small, chunk 110 | 0,893 / 0,823 | 0,750 / 0,650 |
| Híbrido, e5-small, chunk 256 | 0,893 / 0,823 | 0,750 / 0,650 |
| Híbrido + reranker ms-marco (88 MB), topo 30 | 0,964 / 0,854 | 0,750 / 0,600 |
| Híbrido + reranker mmarco (470 MB), topo 30 | 0,964 / 0,854 | 0,833 / 0,600 |
| Híbrido + reranker mmarco, topo 20 | 0,964 / 0,854 | 0,750 / 0,650 |
| Híbrido + reranker mmarco, topo 15 | 0,964 / 0,854 | 0,750 / 0,650 |
| Híbrido + consulta local (glossário) | 0,964 / 0,885 | 0,625 / 0,600 |
| Híbrido + consulta (variantes da LLM) | 0,964 / 0,708 | 0,750 / 0,600 |
| Híbrido + MMR (λ 0,7) | 0,964 / 0,854 | 0,750 / 0,600 |

**Latência (ms, média / p95, dev / reservado).** Valores entre parênteses e marcados "ocupada" têm
`latencia_confiavel=false` (medidos com a máquina ocupada por outros processos): **não tire conclusões de latência
deles**.

| Configuração | Latência média / p95 (ms) dev / res |
|---|---|
| Híbrido legado (estado atual) | 115 / 145 / 91 / 116 |
| Híbrido, e5-small, chunk 110 | 168 / 189 / 90 / 108 |
| Híbrido, e5-small, chunk 256 | 95 / 110 / 95 / 112 |
| Híbrido + reranker ms-marco (88 MB), topo 30 | (440 / 546) ocupada / 392 / 491 |
| Híbrido + reranker mmarco (470 MB), topo 30 | (691 / 774) ocupada / 644 / 730 |
| Híbrido + reranker mmarco, topo 20 | (489 / 569) ocupada / (574 / 891) ocupada |
| Híbrido + reranker mmarco, topo 15 | (453 / 671) ocupada / (464 / 698) ocupada |
| Híbrido + consulta local (glossário) | 124 / 153 / 123 / 147 |
| Híbrido + consulta (variantes da LLM) | 160 / 188 / 155 / 187 |
| Híbrido + MMR (λ 0,7) | 118 / 135 / 93 / 108 |

Modos vetorial e lexical (para referência; o modo de produção é o híbrido):

| Modo | Recall@5 dev / res | MRR dev / res | nDCG@5 dev / res | Acerto no 1º dev / res | Fora do domínio dev / res |
|---|---|---|---|---|---|
| vetorial, legado | 0,781 / 0,775 | 0,735 / 0,770 | 0,704 / 0,707 | 0,641 / 0,688 | 1,000 / 1,000 |
| lexical, legado | 0,917 / 0,838 | 0,809 / 0,775 | 0,811 / 0,756 | 0,697 / 0,700 | 0,833 / 0,833 |
| vetorial, e5-small, chunk 110 | 0,834 / 0,773 | 0,790 / 0,739 | 0,762 / 0,697 | 0,704 / 0,650 | 1,000 / 1,000 |
| lexical, e5-small, chunk 110 | 0,917 / 0,838 | 0,809 / 0,775 | 0,811 / 0,756 | 0,697 / 0,700 | 0,833 / 0,833 |
| vetorial, e5-small, chunk 256 | 0,835 / 0,763 | 0,788 / 0,752 | 0,760 / 0,700 | 0,697 / 0,675 | 1,000 / 1,000 |
| lexical, e5-small, chunk 256 | 0,926 / 0,833 | 0,826 / 0,774 | 0,827 / 0,751 | 0,725 / 0,688 | 0,917 / 0,833 |

**O que os números sustentam:**

1. **Embeddings e5-small: sem ganho consistente sobre o modelo atual no modo híbrido.** Com chunk 110 o Recall@5
   fica igual no dev (0,920 contra 0,919) e menor no reservado (0,834 contra 0,852), e MRR e nDCG@5 caem nos dois;
   com chunk 256 o Recall@5 cai no dev (0,909) e sobe pouco no reservado (0,859), e o MRR cai no reservado (0,786
   contra 0,805). O modelo de embeddings **continua o atual**. O e5-base (1,1 GB) **não foi testado**: passa do
   orçamento de ~600 MB de modelos e o e5-small não mostrou ganho.
2. **Reescrita de consulta: sem ganho.** Com variantes da LLM, o híbrido fica um pouco pior no dev (Recall@5 0,906,
   MRR 0,815) e neutro no reservado (0,858, 0,808); por tema o resultado é misto (no reservado `quantum_info`
   cai de 0,854 para 0,708). A variante determinística do glossário sozinha também não mostra ganho consistente
   (dev 0,903; reservado 0,869 com nDCG@5 0,773). A camada **continua desligada**.
3. **MMR: reduz o recall nos dois conjuntos** e continua desligado (seção anterior).
4. **Reranker (decidido pelo dono em 28/11/2026: ligado na implantação Docker, aceitando modelo acima de 600 MB e p95 acima de 600 ms; fora do Docker segue desligado):**
   - `ms-marco-MiniLM-L6` (88 MB, só inglês): efeito pequeno e misto (dev: MRR 0,860 e acerto no 1º 0,789;
     reservado: MRR 0,797, nDCG@5 0,782, Recall@5 praticamente igual ao do híbrido atual); leva a recusa fora do
     domínio a 1,00 nos dois conjuntos; p95 de 491 ms no reservado (medição confiável, dentro de 600 ms).
   - `mmarco-mMiniLMv2` (470 MB, multilíngue): ganho grande e consistente nos dois conjuntos em MRR (0,921 / 0,885),
     nDCG@5 (0,889 / 0,843) e acerto no 1º lugar (0,866 / 0,825), com recusa fora do domínio de 0,917 no dev e 1,00 no
     reservado. Mas o p95 no reservado é 730 ms (medição confiável, máquina livre), **acima da meta de 600 ms do
     plano** (média de 644 ms contra 91 ms do híbrido atual: cerca de +0,55 s por consulta nesta CPU). Reduzir os
     candidatos (topo 20 e 15) piorou a qualidade nos dois conjuntos (MRR 0,907 / 0,868 e 0,903 / 0,868, contra
     0,921 / 0,885 no topo 30) e as latências dessas execuções não são confiáveis (máquina ocupada), então não se
     sabe quanto a latência melhora.
   - O mmarco (~470 MB) junto com o modelo de embeddings atual (MiniLM, ~470 MB) passaria do orçamento de ~600 MB;
     o modelo atual mais o ms-marco (~558 MB) cabe.
   - Fatos para decidir: ativar o mmarco (qualidade bem maior, ~+0,6 s por consulta nesta CPU e +470 MB), ativar o
     ms-marco (dentro do orçamento, ganho pequeno) ou deixar desligado. A chamada à LLM depois da recuperação costuma
     levar segundos, então +0,6 s seria uma fração do total, mas isso é **hipótese, não foi medido aqui**.
5. **Metas.** *Recusa fora do domínio = 100%:* atingem 1,00 nos dois conjuntos o híbrido + ms-marco, o híbrido + MMR e
   o modo vetorial (que não é o de produção); o mmarco topo 30 atinge só no reservado (0,917 no dev) e a reescrita
   com LLM só no dev; o híbrido atual, o lexical e o híbrido com e5 ficam em 0,833 (o lexical com chunk 256 chega
   a 0,917 no dev). *`medicao` ≥ 0,70 no reservado:* **nenhuma configuração híbrida atinge** (máximo 0,650, com
   e5 e com mmarco topo 20 e 15; o atual e o ms-marco ficam em 0,600); só o modo vetorial legado chega a 0,750 no
   reservado, com qualidade geral pior. No dev todas atingem, exceto a consulta local (0,625). *`quantum_info`
   ≥ 0,70:* atingida por todas as configurações nos dois conjuntos (mínimo 0,708, reservado com reescrita da LLM).
6. **Verificação de respostas e orçamento de contexto: não medidos com respostas reais da LLM.** Medir exigiria
   gerar respostas e não há gabarito de frases sem suporte; o limiar 0,5 continua sem calibração para texto da LLM
   (a calibração de 0,70 vale só para respostas extrativas).

**Dois defeitos reais só apareceram medindo com modelos reais, e foram corrigidos:** `reranker.disponivel()` não
reconhecia um cache enxuto do Hugging Face (sem os pesos duplicados), e as flags de avaliação vazavam de um modo para
o seguinte.

**Como ligar a opção escolhida fora do Docker** (a decisão do dono foi ligar o mmarco por padrão apenas na implantação Docker; fora dele nada está ligado por padrão). Defina no
ambiente da API, por exemplo:

```bash
RAG_RERANKER=1
RAG_RERANKER_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1   # ou a pasta local do modelo
RAG_RERANKER_TOPO=30                                             # opcional (padrão 30)
```

Para o ms-marco, use `cross-encoder/ms-marco-MiniLM-L6-v2`. No Docker, ver "Docker: o que o repositório entrega",
acima. Nesta máquina os modelos já estão no cache do Hugging Face
(`~/.cache/huggingface/hub`). Os repositórios trazem pesos duplicados; para baixar só o necessário (o
`reranker.disponivel()` apenas confere o `config.json` no cache e nunca baixa nada):

```python
from huggingface_hub import snapshot_download
snapshot_download("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                  ignore_patterns=["*.bin", "onnx/*", "openvino/*", ".eval_results/*", "*.onnx", "*.h5",
                                   "*.msgpack", "*.ot", "tf_model*", "flax_model*", "*.md"])
```

**Como reproduzir as medições.** Rode `--split dev` e `--split reservado` como acima, com o modelo já no cache e
`HF_HUB_OFFLINE=1`. Notas sobre os modos:

Cada modo da avaliação fixa todas as flags `RAG_RERANKER`, `RAG_CONSULTA`, `RAG_MMR`, `RAG_ORCAMENTO` e
`RAG_VERIFICACAO` (em `0`, exceto a que o próprio modo liga) e as restaura depois; então flags exportadas no
shell não contaminam as comparações, e o reranker só é ligado no modo `hibrido+reranker`. Sem o modelo no
cache, esse modo é ignorado com um aviso.
**Modos da camada de consulta.** A avaliação tem dois modos para a reescrita de consulta; ambos fixam as
demais flags em `0` e passam as consultas explicitamente a `buscar_documentos(..., consultas=[...])`:
- `hibrido+consulta_local`: sempre disponível e sem LLM. Usa `consulta.variantes_locais(pergunta)`, a pergunta
  mais a variante determinística do glossário (o mesmo que `expandir_consulta` devolve sem chave).
- `hibrido+consulta`: só entra com `--variantes ARQUIVO.json`, um objeto `{"<id do caso>": ["pergunta original",
  "variante 1", ...]}` gravado antes (reprodutível sem rede). O formato é validado antes de carregar o
  corpus; ids desconhecidos no split avaliado são erro (`parser.error` os lista); ids ausentes usam só a pergunta.
- `--gerar-variantes ARQUIVO.json`: chama `expandir_consulta` de verdade (exige `NVIDIA_API_KEY` no ambiente),
  para cada caso do `--split`, grava o arquivo de forma atômica (a cada 20 casos e ao final), retoma pulando ids
  já gravados, imprime quantos casos receberam variantes da LLM e quantos ficaram só com o fallback e sai sem avaliar.

Detalhes de `--gerar-variantes`: cada caso é repetido até 2 vezes (pausa de 1 s) quando a reescrita falha
(timeout, 429, rede, JSON malformado), detectado pelo traço do fallback; **resultado de falha nunca é gravado**,
então uma nova execução o refaz. Após 5 casos seguidos falhando, a geração para, salva o progresso e sai com
código 1 ("a API parece indisponível ou sem cota"); o código também é 1 se restar qualquer caso pendente. O
resumo traz três contagens: com variantes da LLM, pendentes por falha e já presentes de execução anterior. Um
arquivo existente corrompido ou malformado é recusado (código 2) antes de qualquer chamada, e Ctrl+C ou erro
inesperado salvam o que já foi gerado. Sem nada novo, o arquivo não é reescrito.
Limitação de `hibrido+consulta_local`: perguntas sem termos extras do glossário ficam com uma só consulta e
seguem o caminho de consulta única (idêntico ao `hibrido` simples), então o ganho desse modo é diluído.

As 240 variantes gravadas da LLM estão em `docs/benchmarks/variantes-consulta-2026-11-28.json` (geradas com
`--gerar-variantes`) e podem ser reutilizadas offline com `--variantes`. O validador recusa ids fora do split
avaliado, então separe o arquivo por split antes (comando que usa o campo `split` de `tests/rag_benchmark.json`):

```bash
python -c "import json;c=json.load(open('tests/rag_benchmark.json',encoding='utf-8'));v=json.load(open('docs/benchmarks/variantes-consulta-2026-11-28.json',encoding='utf-8'));[json.dump({x['id']:v[x['id']] for x in c if x['split']==s and x['id'] in v},open(f'variantes-{s}.json','w',encoding='utf-8'),ensure_ascii=False) for s in ('dev','reservado')]"
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split dev --variantes variantes-dev.json --saida rag-consulta-dev.json
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split reservado --variantes variantes-reservado.json --saida rag-consulta-reservado.json
```

### Camadas combinadas (28/11/2026, todas ligadas no Docker)

Depois de o dono pedir todas as camadas ligadas, a combinação foi medida no mesmo gabarito (240 casos, K = 5,
CPU local, modelo `mmarco` no cache). Novos modos da avaliação: `hibrido+reranker+mmr`, `hibrido+reranker+consulta`
e `hibrido+completo` (reranker + MMR + reescrita pelas variantes gravadas da LLM), escolhidos com `--modos`.
Dados: `docs/benchmarks/rag-camadas-combinadas-2026-11-28-dev.json` e `...-reservado.json`. O orçamento de contexto
e a verificação não mudam a ordem da recuperação, então não aparecem nestas métricas.

| Configuração (dev / reservado) | Recall@5 | MRR | nDCG@5 | Acerto no 1º | Fora do domínio | p95 (ms) |
|---|---|---|---|---|---|---|
| Híbrido (sem camadas) | 0,919 / 0,852 | 0,833 / 0,805 | 0,828 / 0,765 | 0,725 / 0,700 | 0,833 / 0,833 | 121 / 103 |
| Híbrido + reranker | 0,933 / 0,887 | 0,921 / 0,885 | 0,889 / 0,843 | 0,866 / 0,825 | 0,917 / 1,000 | 829 / 783 |
| Híbrido + reranker + MMR | 0,885 / 0,829 | 0,873 / 0,866 | 0,840 / 0,799 | 0,824 / 0,800 | 1,000 / 1,000 | 813 / 808 |
| Híbrido + reranker + reescrita | 0,915 / 0,898 | 0,885 / 0,885 | 0,860 / 0,848 | 0,817 / 0,813 | 1,000 / 1,000 | 847 / 819 |
| Tudo ligado (reranker + MMR + reescrita) | 0,898 / 0,860 | 0,874 / 0,870 | 0,846 / 0,816 | 0,810 / 0,800 | 1,000 / 1,000 | 890 / 869 |

O que os números mostram (latências medidas com outros processos rodando; use só como ordem de grandeza):

1. **O MMR piora a recuperação também junto com o reranker**: com tudo ligado, Recall@5, MRR, nDCG@5 e acerto no
   1º ficam abaixo do reranker sozinho nos dois conjuntos (reservado: Recall@5 0,860 contra 0,887; MRR 0,870 contra
   0,885). Ao diversificar, ele tira do topo trechos relevantes parecidos entre si.
2. **A reescrita junto com o reranker é neutra**: no reservado, Recall@5 sobe (0,898 contra 0,887) e o MRR fica
   igual (0,885); no dev, Recall@5 e MRR caem um pouco (0,915 e 0,885 contra 0,933 e 0,921).
3. **Tudo ligado ainda é melhor que nenhuma camada** (reservado: MRR 0,870 contra 0,805; acerto no 1º 0,800 contra
   0,700) e recusa 100% das perguntas fora do domínio nos dois conjuntos, mas é pior que só o reranker.
4. **Verificação nas respostas reais da LLM** (teste ao vivo, 28/11/2026): uma resposta didática para leigo teve
   fidelidade 0,10 (37 de 41 frases sem apoio direto nas fontes, por causa de analogias e explicações) e uma resposta
   técnica para especialista teve 0,39. Por isso, em `/analise` a verificação só informa (campo `verificacao`);
   em `/query`, com `RAG_MIN_FIDELIDADE=0,6`, respostas assim seriam descartadas e trocadas pela resposta extrativa.

Por isso a implantação desliga o MMR (`RAG_MMR=0`); as outras camadas ficam ligadas (reescrita neutra, orçamento e
verificação não mudam a ordem da busca).

## Traço da resposta (`trace`)

Toda resposta de `POST /query` (e das conversas) traz o campo opcional `trace`, inclusive quando o status é
`insufficient_context`:

```json
{"etapas": {"Obter coleção (inclui inicialização se necessária)": 0.001, "TOTAL": 0.1},
 "consultas": ["pergunta usada na busca"], "candidatos": 3,
 "camadas": {"reranker": false, "consulta": false, "mmr": false, "orcamento": false, "verificacao": false},
 "modelo_embeddings": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
 "fallbacks": []}
```

- `etapas`: segundos por etapa da recuperação (`TOTAL` inclui tudo); `consultas`: consultas realmente usadas
  (só a original quando a camada de consulta está desligada); `candidatos`: seções recuperadas; `camadas`:
  flags `RAG_*` ativas; `fallbacks`: motivos de camadas ligadas que caíram no comportamento anterior
  (`reranker: ...`, `mmr: ...`, `consulta: ...`, `verificacao: ...`).
- Os fallbacks são coletados por requisição (`rag/traco.py`, `ContextVar`) e não vazam entre requisições.
- O campo existe em `/query` e nas conversas. A análise unificada (`orquestrador.py`) não chama
  `rag.pipeline.responder`, por isso `RespostaAnalise` não traz `trace`.
- O esquema está em `openapi.json` (`TracoRAG`), gerado por `python exportar_openapi.py`.
