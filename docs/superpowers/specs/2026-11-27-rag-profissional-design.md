# RAG profissional do QuantumOne — design

Data: 2026-11-27 · Status: aguardando aprovação do dono do projeto

## Intenção e porquê
Levar o RAG a um padrão profissional: respostas mais corretas, mais confiáveis e rastreáveis, sem tornar o
pacote instalável pesado. Hoje o RAG é híbrido (vetorial + BM25 + RRF + regras) e recusa perguntas sem
evidência, com Recall@5 0,827 e MRR 0,911 num gabarito de 47 perguntas. Limites identificados:

1. Embeddings `paraphrase-multilingual-MiniLM-L12-v2` (feito para paraphrase, não recuperação), chunks presos a 110 tokens.
2. Sem reranker neural; reordenação por regras.
3. Consulta usada como veio (sem reescrita/multi-query); temas fracos: `quantum_info` (recall 0,42), `medicao` (0,50).
4. Evidência medida por sobreposição de termos; nada verifica se cada afirmação da resposta é sustentada pelas fontes.
5. Benchmark pequeno e escrito pela mesma equipe do conhecimento.

## Objetivos
- Recall@5, MRR e nDCG@5 acima da linha de base atual no benchmark ampliado (casos antigos incluídos).
- Temas fracos com recall ≥ 0,70.
- Fidelidade da resposta medida (nova métrica) e abstenção calibrada: fora do domínio continua 100% recusado.
- Cada camada é ligada/desligada por variável de ambiente e só vira padrão se melhorar as métricas.
- Rastreamento por etapa (tempos, candidatos, motivos) em cada resposta.

## Não objetivos
- Não adicionar busca web (foi removida de propósito).
- Não trocar ChromaDB, nem o provedor de LLM (NVIDIA continua opcional).
- Não usar modelos acima de ~600 MB no total (decisão do dono: opção A).

## Anti-objetivos (falha mesmo que "funcione")
- Piorar latência a ponto de a conversa parecer travada (meta: retrieval p95 ≤ 600 ms em CPU, sem LLM).
- Depender da chave NVIDIA para o RAG funcionar: sem chave ou com falha, tudo cai para o caminho local.
- Superajustar ao gabarito: usar conjunto de desenvolvimento e conjunto reservado.
- Apagar ou corromper a coleção atual durante a migração.

## Restrições
- `banco.py` recusa coleção com modelo diferente; modelo novo = coleção nova (blue/green), a antiga permanece.
- O limite de 110 tokens está fixo em `configuracao.limite_tokens`, `indexar_documentos.validar_lote` e
  `preparar_chunks`; precisa virar função do modelo.
- Modelos carregam com `local_files_only` por padrão; download só com `GROWUP_PERMITIR_DOWNLOAD=1` e autorização explícita.
- O working tree atual tem muitas mudanças não commitadas: trabalho em branch próprio.
- Servidor ChromaDB via HTTP; embeddings gerados no cliente.

## Abordagens consideradas
1. **Camadas incrementais com portão por métrica — ESCOLHIDA.** Permite atribuir ganho/regressão a cada parte e reverter uma camada sozinha.
2. Reescrita de uma vez: rápida no papel, sem atribuição de causa, risco alto. Descartada.
3. Só recuperação, sem camadas de LLM: mais leve, mas sem consulta inteligente nem verificação. Descartada.

## Desenho (ordem de execução)
0. **Branch e linha de base.** Branch `feat/rag-profissional`. Congelar os números atuais.
1. **Benchmark ampliado** (`tests/rag_benchmark.json` + `rag/avaliacao_rag.py`): ~150 casos (PT/EN, paraphrases,
   perguntas curtas/ambíguas, adversariais, fora do domínio), divisão dev/reservado, métrica de fidelidade
   (suporte de cada frase da resposta pelas fontes), relatório comparativo por camada.
2. **Embeddings + chunking** (`configuracao.py`, `preparar_chunks.py`, `banco.py`, `indexar_documentos.py`):
   `intfloat/multilingual-e5-base` (alternativa `-small` se a latência exigir), prefixos `query: `/`passage: `,
   limite de tokens derivado do modelo (alvo ~256), nome de coleção versionado, script de reindexação sem
   downtime, `RAG_EMBEDDING_MODEL` escolhe o ativo.
3. **Reranker** (`rag/reranker.py`, novo): cross-encoder multilíngue pequeno sobre o top-30 do RRF; as regras
   antigas viram sinais auxiliares. Carregamento preguiçoso, com fallback para a ordem RRF se o modelo faltar.
4. **Entendimento da consulta** (`rag/consulta.py`, novo; usa `interpretacao.py`): reescrita autônoma (inclui
   histórico da conversa), multi-query e decomposição, via LLM NVIDIA com timeout curto; fallback por regras +
   glossário; fusão dos resultados por RRF; cache por consulta.
5. **Montagem de contexto** (`contexto.py`, `busca_chromadb.py`): MMR para diversidade, expansão para a seção,
   orçamento de contexto por relevância em vez de corte fixo.
6. **Verificação da resposta** (`rag/verificacao.py`, novo): cada frase com citação é checada contra a fonte
   (similaridade de embedding + sobreposição de termos; juiz LLM opcional); frases sem suporte são sinalizadas
   ou removidas; abstenção calibrada no conjunto de desenvolvimento.
7. **Observabilidade e documentação:** traço por etapa na resposta, métricas de latência, atualização de
   `docs/RAG.md`, `docs/BENCHMARKS.md`, `.env.example` e `openapi.json`.

Portão de cada camada (2–6): melhora Recall@5/MRR/nDCG ou fidelidade no conjunto de desenvolvimento, sem piorar
o reservado nem a recusa fora do domínio, dentro do orçamento de latência. Caso contrário, fica desligada.

## Decisões do dono
- Peso/custo: opção A (modelos médios, ~300–600 MB; LLM NVIDIA opcional para consulta e verificação).
- Abordagem 1 (camadas incrementais).

## Minhas decisões (para conferir)
- Embeddings e5 em vez de bge-m3 (menor e suficiente para o orçamento); reavalio com `-small` se o p95 estourar.
- Chunks de ~256 tokens, ajustáveis pelo benchmark.
- Branch `feat/rag-profissional`; sem commit sem pedido do dono (este documento fica sem commit por enquanto).
- Cross-encoder escolhido na etapa 3, comparando 2 candidatos no benchmark.

## Fica para quem constrói
- Escolha final do cross-encoder e dos limiares (cobertura, verificação) por calibração.
- Formato exato do traço e dos novos campos de API (`openapi.json` precisa ser atualizado).
- Estratégia de download dos modelos (script explícito, nunca silencioso).
