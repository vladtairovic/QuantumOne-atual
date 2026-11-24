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
- Sem reranker neural; a resposta local é extrativa e não combina fontes.
- OpenQASM 3 é convertido só no subconjunto básico (declarações, portas, medições); controle de
  fluxo, sub-rotinas e temporização de 3.0 não são analisados.
- O interpretador Python aceita apenas construção de circuitos (sem `Parameter` na otimização, sem
  funções ou bibliotecas externas); o restante é ignorado na validação e listado.
- Circuitos não Clifford de 20 a 30 qubits com muito emaranhamento podem ficar "não verificados".
- MPS é recomendado, mas não é usado na verificação.

## Melhorias futuras recomendadas
1. Sincronizar o catálogo completo em produção e ampliar o gabarito de RAG com perguntas de outras pessoas.
2. Reranker cross-encoder local, avaliado com o mesmo gabarito.
3. Verificação por MPS (qiskit-aer) para 20–30 qubits com pouco emaranhamento.
4. Parser completo de OpenQASM 3 (dependência `qiskit-qasm3-import`).
5. Suporte a `Parameter` no otimizador, com verificação para valores amostrados.
6. Otimização ZX (pyzx), se um benchmark mostrar ganho no conjunto de portas do projeto.
