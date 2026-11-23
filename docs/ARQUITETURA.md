# Arquitetura do QuantumOne (QOne)

Este documento descreve a arquitetura após a reestruturação de novembro de 2026
(prioridades 0 a 7). Os detalhes de cada área estão em:

- [RAG.md](RAG.md): recuperação, evidência, respostas e avaliação.
- [BASE-DE-CONHECIMENTO.md](BASE-DE-CONHECIMENTO.md): fontes, curadoria e atualização.
- [MOTOR-QUANTICO.md](MOTOR-QUANTICO.md): diagnóstico, correção, otimização e verificação.
- [BENCHMARKS.md](BENCHMARKS.md): resultados medidos, problemas resolvidos e limitações.
- [LLM.md](LLM.md): fila de prioridade de LLMs (NVIDIA), limite de 15 s por modelo e fallback.

## Visão geral

```
Desktop Chat (Next.js)  ─┐
                         ├─> API FastAPI (main.py) ─┬─> RAG local (rag/) ──> ChromaDB + BM25 em memória
CLI (qone_cli.py)       ─┘                          │                    └─> Gemini (opcional, sem ferramenta de busca)
                                                    └─> Worker isolado (quantum/worker.py)
                                                         ├─ diagnóstico/correção (quantum/diagnostico*.py)
                                                         └─ otimização/verificação (quantum/otimizador.py, verificacao.py)
```

O Desktop Chat e o CLI usam as mesmas rotas HTTP. Não há lógica de domínio
no cliente: o frontend apenas exibe o que a API devolve.

## Fluxos

| Fluxo | Caminho |
|---|---|
| Pergunta | `POST /query` → `rag.interpretacao` → `busca_chromadb` (híbrida) → `avaliar_evidencia` → Gemini **ou** `resposta_local` |
| Código inválido | `POST /qiskit/optimize` recusa → frontend chama `POST /qiskit/diagnose` → todos os problemas, correção, validação |
| Circuito válido | `POST /qiskit/optimize` → análise estrutural → candidatos por perfil → verificação → relatório comparativo |
| Conversa | `POST /conversations/{id}/messages` (mesmo pipeline de `/query`, com histórico) |
| CLI | `python qone_cli.py perguntar|buscar|diagnosticar|otimizar|limites|saude` |

## Componentes

### Backend (`main.py`, `servicos.py`, `contratos.py`)
- Contratos Pydantic com `extra='forbid'`; o OpenAPI (`openapi.json`) é gerado por `exportar_openapi.py` e conferido em teste.
- `web_search` continua aceito por compatibilidade, mas é ignorado e gera um aviso.
- Novos campos em `/query`: `answer_mode` (`llm`, `local`, `none`), `interpretation` e `evidence`.

### RAG (`rag/`)
| Módulo | Papel |
|---|---|
| `texto.py` | tokenização sensível a código (`QuantumCircuit.cx` → `quantumcircuit`, `cx`…), stopwords PT/EN |
| `glossario.py` | expansão PT→EN de termos técnicos |
| `interpretacao.py` | tipo de entrada, linguagem, versão OpenQASM, biblioteca, intenção, complexidade, hipóteses |
| `busca_chromadb.py` | busca vetorial + BM25, fusão RRF, reordenação, montagem de seções, verificação de evidência |
| `resposta_local.py` | resposta extrativa sem LLM (offline), com citações literais |
| `pipeline_gemini.py` | orquestra a consulta; Gemini opcional, fallback local |
| `conhecimento_interno.py` | carrega e indexa `documentos/conhecimento_interno/*.md` |
| `curadoria.py` | deduplicação, boilerplate, classificação e detecção de API obsoleta |
| `coleta_web.py`, `sincronizar_conhecimento.py` | coleta **administrativa** de documentação por allowlist |
| `avaliacao_rag.py` | avaliação reproduzível (Recall@K, MRR, nDCG…) |

O antigo `rag/busca_web.py` (Google Search via Gemini) foi removido.

### Motor quântico (`quantum/`)
| Módulo | Papel |
|---|---|
| `diagnostico.py` | orquestra o diagnóstico de entradas heterogêneas e tracebacks |
| `diagnostico_qasm.py` | analisador tolerante de OpenQASM 2 (e subconjunto de 3) com todas as falhas de uma vez |
| `diagnostico_python.py` | análise estática de Python/Qiskit (AST, sem executar) |
| `migracao.py` | regras de APIs removidas do Qiskit, verificadas contra o Qiskit instalado |
| `analise.py` | métricas estruturais, redundâncias, gargalos, CX equivalentes, método de simulação |
| `otimizador.py` | perfis, candidatos, mapas de acoplamento, portas nativas, escolha verificada |
| `verificacao.py` | equivalência exata/aproximada/amostral/estatística |
| `python_parser.py` | interpretador restrito de construção (agora com `if_test` e `qc.qubits`/`qc.clbits`) |
| `worker.py` | subprocesso descartável com limites de CPU, memória e arquivos (Linux) |

### Frontend (`frontend/`)
- Removidos: opção "Buscar também na web", ferramenta Terminal e seus estilos.
- Novo `CompactSources`: "Fontes consultadas (N)", recolhido por padrão, expansão animada.
- Painel de resultado mostra modo da resposta, motor (perfil, verificação, CX equivalentes, simulação, compromissos) e diagnóstico (problemas, causa, correção, alternativas, código corrigido).
- Seletor de perfil de otimização no compositor.

## Decisões técnicas

1. **Resposta local por padrão sem LLM.** Sem chave (ou com falha/cota do Gemini) a API não devolve 503: responde com trechos literais da base. `QONE_LOCAL_FALLBACK=0` restaura o 503 `LLM_NOT_CONFIGURED`.
2. **Nenhuma rede durante consultas.** Busca web removida; `KNOWLEDGE_AUTO_SYNC=0` por padrão. A coleta de documentação é um processo administrativo explícito.
3. **Busca híbrida sem novos serviços.** O BM25 roda em memória sobre os trechos do próprio ChromaDB e é invalidado a cada gravação; não há novo banco nem dependência.
4. **Diagnóstico nunca executa código.** QASM é analisado por regras e validado pelo parser do Qiskit; Python é analisado por AST e validado pelo interpretador restrito.
5. **Otimização só é aceita se não for refutada.** O original é candidato quando não há hardware-alvo: o motor não piora a métrica prioritária.
6. **Verificação graduada.** Cada resultado informa o método e a garantia (`exata`, `aproximada`, `amostral`, `estatistica`, `nenhuma`).

## Dependências novas

| Pacote | Onde | Motivo |
|---|---|---|
| `qiskit-aer==0.18.4` | `requirements-api.txt` | verificação por amostragem de circuitos dinâmicos; MPS (recomendação de simulação) |

Nenhuma dependência foi adicionada ao frontend.

## Variáveis de ambiente novas

| Variável | Padrão | Efeito |
|---|---|---|
| `QONE_LLM` | `auto` | `local` nunca chama LLM |
| `NVIDIA_API_KEY` / `NVIDIA_MODELS` | vazio | fila NVIDIA (prioridade sobre o Gemini) e ordem dos modelos |
| `LLM_TIMEOUT_SECONDS` / `LLM_PAUSA_SEGUNDOS` / `LLM_TEMPO_TOTAL_SECONDS` | `15` / `300` / `45` | prazo por modelo, pausa após falha, teto da fila |
| `QONE_LOCAL_FALLBACK` | `1` | `0` devolve 503 sem LLM |
| `RAG_MODO` | `hibrido` | `vetorial` ou `lexical` |
| `RAG_MIN_COBERTURA` | `0.34` | cobertura mínima de termos para responder |
| `RAG_BONUS_TITULO` | `1.2` | reforço quando o título nomeia o conceito |
| `QONE_LOCAL_CORTE` | `0.7` | fração da melhor pontuação usada na resposta local |
| `QONE_MAX_STATEVECTOR_QUBITS` | `22` | limite do teste por estados aleatórios |
| `QONE_STATEVECTOR_BUDGET` | `6e8` | orçamento amplitudes × portas × estados |
| `QONE_TOL_INFIDELIDADE_APROX` | `1e-8` | limite da equivalência aproximada |
| `KNOWLEDGE_AUTO_SYNC` | `0` | coleta periódica (antes `1`) |

Removidas: `WEB_SEARCH_ENABLED`, `GEMINI_WEB_MODEL`.
