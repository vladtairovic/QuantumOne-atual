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
