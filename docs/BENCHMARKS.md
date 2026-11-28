# Testes, benchmarks e resultados (novembro de 2026)

Todos os números abaixo foram produzidos pelos scripts do repositório e estão gravados em
`docs/benchmarks/`. Ambiente: Windows 11, Python 3.12.10, Qiskit 2.6.0, qiskit-aer 0.18.4, CPU.

## Como reproduzir

```bash
python scripts/benchmark_qone.py --saida docs/benchmarks/motor-balanceado.json --perfil balanceado
python scripts/benchmark_qone.py --saida docs/benchmarks/motor-hardware-linear.json --perfil avancado --hardware linear
python scripts/benchmark_diagnostico.py --saida docs/benchmarks/diagnostico.json
GROWUP_PERMITIR_DOWNLOAD=1 python -m rag.avaliacao_rag --saida docs/benchmarks/rag-avaliacao.json
python -m pytest -q
```

A **linha de base** (`linha-de-base.json`) foi medida com o mesmo script sobre o código anterior
(commit `545b039`, `processar_circuito` com um único `transpile` de nível 3). Os CX equivalentes são
medidos pelo próprio script a partir dos QASM de entrada e de saída, independentemente do motor.

## Motor de circuitos

Suíte: 52 circuitos de 2, 4, 5, 8, 12, 16, 20, 25 e 30 qubits, nas categorias aleatório, GHZ, QFT
(até 20 qubits), redundante, emaranhado (brickwork) e com medições.

| Métrica | Linha de base | Balanceado | Avançado | Avançado + linha + `ibm_cx` |
|---|---|---|---|---|
| Equivalência verificada | 20/52 (38%) | **48/52 (92%)** | 48/52 (92%) | 45/52 (87%) |
| Maior circuito verificado | 8 qubits | **30 qubits** | 30 qubits | 30 qubits |
| Propostas refutadas / falhas | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Variação do total de portas | +2,8% | **−42,8%** | −42,8% | +145% (roteamento + tradução) |
| Variação da profundidade | +6,5% | **−34,5%** | −34,5% | +109% |
| Variação das portas de 2 qubits | +22,8% | **−13,6%** | −13,6% | +105% |
| Variação dos CX equivalentes | −9,8% | −9,8% | −9,8% | +50% |
| Tempo total da suíte | 2,8 s | 9,6 s | 9,7 s | 35,9 s |

Leitura honesta:
- O ganho principal está na **verificação** (de 38% para 92% dos casos, de 8 para 30 qubits) e no
  fim das regressões de portas, profundidade e portas de 2 qubits (o motor antigo aumentava essas
  métricas ao decompor SWAP/CU; o novo mantém o original quando ele é melhor).
- A redução de **CX equivalentes ficou igual** à da linha de base (9,8%) nesta suíte: os ganhos de
  CX vêm do mesmo transpilador, e os circuitos aleatórios e brickwork têm pouca redundância
  explorável. Não alegamos ganho de CX sem hardware.
- Sem hardware-alvo, **avançado = balanceado**: variar a semente só muda o layout/roteamento. Com
  topologia linear, o avançado usou 3.487 CX contra 3.569 do balanceado (−2,3%), com 1,7× mais tempo.
- Com hardware, o aumento de portas é o custo do roteamento para uma linha e da tradução para
  {cx, rz, sx, x}. A linha de base não oferecia hardware-alvo, então não há comparação direta.
- Não verificados (balanceado): aleatório de 20, 25 e 30 qubits e QFT de 20 qubits, sem método
  exato nem amostral dentro do orçamento padrão. Com hardware, também os brickwork de 20 a 30 qubits.
- Um caso de equivalência **aproximada** (QFT de 16 qubits no hardware linear, infidelidade 5,7×10⁻¹⁰)
  foi identificado e relatado como tal. Antes da correção ele era rejeitado.

## Diagnóstico e correção

39 casos (`scripts/benchmark_diagnostico.py`): 34 danificados (18 OpenQASM, 13 Python, 3 tracebacks,
inclusive casos com 4 a 6 erros simultâneos) e 5 válidos.

| Métrica | Resultado |
|---|---|
| Taxa de detecção dos erros esperados | 100% (34/34 casos com todos os erros) |
| Precisão (erros reportados que eram esperados) | 100% |
| Correções aceitas pelo carregador e sem erros na reanálise | 97,2% (35/36 com código) |
| Falsos positivos em entradas válidas | 0 |
| Tempo médio | ~1 ms |

O caso não validado (`p_aer`) não contém circuito: a migração de `Aer` foi aplicada, mas não há
`QuantumCircuit` para validar. O conjunto foi escrito junto com o motor, então mede regressões e
cobertura das regras; não é uma avaliação independente.

## RAG

Ver [RAG.md](RAG.md): Recall@5 0,671 → **0,827**, MRR 0,750 → **0,911**, nDCG@5 0,625 → **0,808**,
acerto no 1º lugar 0,660 → **0,851**, precisão das citações locais 0,583 → **0,699**, 100% das
perguntas fora do domínio recusadas.

## RAG: novo benchmark de 240 casos (dev e reservado)

Relatórios `docs/benchmarks/rag-avaliacao-dev.json` (154 casos) e `rag-avaliacao-reservado.json` (86 casos),
gerados offline em 27/11/2026 com o modelo legado e K = 5. Detalhes, `recall_por_tema` e leitura em
[RAG.md](RAG.md#novo-benchmark-240-casos-divisão-devreservado).

| Modo | Recall@5 dev / reservado | MRR dev / reservado | nDCG@5 dev / reservado |
|---|---|---|---|
| Linha de base (vetorial pura) | 0,767 / 0,744 | 0,729 / 0,711 | 0,697 / 0,658 |
| Híbrido (padrão atual) | 0,919 / 0,852 | 0,833 / 0,805 | 0,828 / 0,765 |
| Híbrido + MMR (opcional, desligado) | 0,881 / 0,817 | 0,806 / 0,803 | 0,797 / 0,747 |

- O MMR reprovou o portão e continua desligado.
- Nenhuma camada opcional virou padrão. Medições com modelos reais em 28/11/2026 (tabelas completas, com dev e
  reservado lado a lado, em [RAG.md](RAG.md#medições-com-modelos-reais-28112026); dados em
  `docs/benchmarks/rag-medicoes-2026-11-28.json`, 32 medições):
  - embeddings e5-small (chunk 110 e 256): sem ganho consistente no híbrido; o modelo atual fica. O e5-base
    (1,1 GB) não foi testado (fora do orçamento de ~600 MB).
  - reescrita de consulta (LLM e só glossário): sem ganho; continua desligada.
  - reranker `ms-marco-MiniLM-L6` (88 MB, inglês): efeito pequeno e misto, recusa fora do domínio 1,00 nos dois
    conjuntos, p95 491 ms no reservado.
  - reranker `mmarco-mMiniLMv2` (470 MB, multilíngue), topo 30, híbrido atual → com reranker (dev / reservado):
    MRR 0,833 / 0,805 → 0,921 / 0,885; nDCG@5 0,828 / 0,765 → 0,889 / 0,843; acerto no 1º 0,725 / 0,700 → 0,866 / 0,825;
    mas p95 de 730 ms no reservado (medição confiável), acima da meta de 600 ms. Topo 20 e 15 pioraram a qualidade e
    suas latências não são confiáveis (máquina ocupada). O mmarco (~470 MB) junto com o modelo de embeddings
    atual (~470 MB) passaria do orçamento de ~600 MB; o modelo atual mais o ms-marco (~558 MB) cabe. Ativar o mmarco, o ms-marco ou nenhum é decisão do dono.
  - verificação de respostas e orçamento de contexto: não medidos com respostas reais da LLM.
- Fora do domínio: o híbrido atual recusa 0,833 dos casos (10/12 no dev, 5/6 no reservado), abaixo da meta de 100%;
  1,00 nos dois conjuntos só com o ms-marco (e com o MMR, que reprova em recall).
- Meta de recall ≥ 0,70 por tema: `quantum_info` atingida em todas as configurações; `medicao` atingida no dev
  (0,750 no híbrido atual), mas no reservado nenhuma configuração híbrida atinge (híbrido atual 0,600, máximo 0,650).
- Defeitos reais achados só medindo com modelos reais e corrigidos: `reranker.disponivel()` com cache enxuto do
  Hugging Face; flags de avaliação que vazavam entre modos.
- Esses números não se comparam com a tabela de 53 casos da seção anterior.

Reprodução:

```bash
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split dev --saida docs/benchmarks/rag-avaliacao-dev.json
HF_HUB_OFFLINE=1 python -m rag.avaliacao_rag --split reservado --saida docs/benchmarks/rag-avaliacao-reservado.json
```

## Testes automatizados

| Suíte | Resultado |
|---|---|
| pytest (Windows, Python 3.12) | todos aprovados; `test_live` roda só com `GROWUP_TEST_URL` |
| pytest na imagem Docker `test` (Linux, Python 3.12, como no CI) | aprovado |
| Frontend: lint, build de produção com checagem de tipos | aprovados |
| Frontend: `tests/analysis-api.test.cjs` | 11/11 |
| `scripts/validar_docker.py` (containers reais) | executado pelo GitHub Actions |

Novos arquivos de teste: `test_sem_busca_web.py` (P0, com bloqueio de rede), `test_conhecimento_interno.py`
(P1: exemplos executados, regras de migração, curadoria, catálogo), `test_diagnostico.py` (P3),
`test_motor_quantico.py` (P4, até 30 qubits), `test_cli.py` (CLI → backend → motor).

## Problemas encontrados e resolvidos

| Problema | Causa | Resolução |
|---|---|---|
| Consultas consumiam a cota gratuita e falhavam com `WEB_SEARCH_UNAVAILABLE` | `WEB_SEARCH_ENABLED=1` por padrão no compose | busca web removida; resposta local |
| Sem chave de LLM, `/query` devolvia 503 | LLM obrigatória | resposta extrativa local (`answer_mode=local`) |
| Perguntas em português recuperavam mal a documentação em inglês | só busca vetorial | BM25 + glossário PT→EN + RRF |
| Parser parava no primeiro erro | `qasm2.loads`/interpretador lançam na 1ª falha | analisadores tolerantes |
| Motor aumentava portas de 2 qubits | decomposição sem comparar com o original | original como candidato, CX equivalentes |
| Equivalência só até 8 qubits | só comparação matricial | Clifford, reescrita, estados aleatórios, amostragem |
| QFT 16q em hardware "não passava" | rotações minúsculas descartadas pelo Qiskit | classe `aproximada` com erro medido |
| Laço extra perdia o layout de roteamento | o PassManager não propaga `layout` | layout copiado do candidato |
| Worker falhava com resultados de Clifford | `numpy.bool_` não serializável | conversão explícita e `default=` no JSON |
| Falso positivo de API obsoleta em páginas com código achatado | regex `[^\n]*` atravessava parágrafos | lista explícita de nomes no import |
| `cnot` do Amazon Braket marcado como obsoleto | regra do Qiskit aplicada a outra biblioteca | regras restritas a bibliotecas Qiskit |
| `with qc.if_test(...)` era recusado | interpretador restrito sem `with` | suporte seguro a `if_test` e `qc.clbits` |

## Limitações conhecidas
- O catálogo verificado (308 fontes) só entra na base depois da sincronização administrativa; a base
  inicial do container tem os snapshots IBM e o conhecimento interno.
- PennyLane não é coletado (o site bloqueia coletores).
- Reranker neural opcional e desligado (medido em 28/11/2026, ver RAG.md); a resposta local é extrativa e não combina fontes.
- OpenQASM 3 é convertido só no subconjunto básico (declarações, portas, medições); controle de
  fluxo, sub-rotinas e temporização de 3.0 não são analisados.
- O interpretador Python aceita apenas construção de circuitos (sem `Parameter` na otimização, sem
  funções ou bibliotecas externas); o restante é ignorado na validação e listado.
- Circuitos não Clifford de 20 a 30 qubits com muito emaranhamento podem ficar "não verificados".
- MPS é recomendado, mas não é usado na verificação.

## Melhorias futuras recomendadas
1. Sincronizar o catálogo completo em produção e ampliar o gabarito de RAG com perguntas de outras pessoas.
2. Decidir o reranker (mmarco ou ms-marco) com base nas medições de 28/11/2026 e reavaliar a latência com a máquina livre.
3. Verificação por MPS (qiskit-aer) para 20–30 qubits com pouco emaranhamento.
4. Parser completo de OpenQASM 3 (dependência `qiskit-qasm3-import`).
5. Suporte a `Parameter` no otimizador, com verificação para valores amostrados.
6. Otimização ZX (pyzx), se um benchmark mostrar ganho no conjunto de portas do projeto.
