# Arquitetura do QuantumOne (QOne)

Este documento descreve a arquitetura após a reestruturação de novembro de 2026
(prioridades 0 a 7). Os detalhes de cada área estão em:

- [RAG.md](RAG.md): recuperação, evidência, respostas e avaliação.
- [BASE-DE-CONHECIMENTO.md](BASE-DE-CONHECIMENTO.md): fontes, curadoria e atualização.
- [MOTOR-QUANTICO.md](MOTOR-QUANTICO.md): diagnóstico, correção, otimização e verificação.
- [BENCHMARKS.md](BENCHMARKS.md): resultados medidos, problemas resolvidos e limitações.
- [LLM.md](LLM.md): fila sequencial de LLMs (NVIDIA), 30 s por modelo, streaming, progresso ao vivo e fallback.

## Visão geral

```
Desktop Chat (Next.js)  ─┐                           POST /analise ─> orquestrador.py
                         ├─> API FastAPI (main.py) ─┬─> RAG local (rag/) ──> ChromaDB + BM25 em memória
CLI (qone_cli.py)       ─┘        ▲                 ├─> Pesquisa web sem chave (rag/pesquisa_web.py)
                                  │                 ├─> Fila sequencial de LLMs NVIDIA (rag/cliente_llm.py)
      GET /analise/progresso/{id} ┘                 └─> Worker isolado (quantum/worker.py)
      (texto ao vivo: etapa e modelo)                    ├─ diagnóstico/correção (quantum/diagnostico*.py)
                                                         └─ otimização/verificação (quantum/otimizador.py, verificacao.py)
```

## Análise unificada (`POST /analise`)

Uma única chamada aceita **texto, código, arquivos soltos e pastas de projeto** (arquivos com caminho
relativo, até 300 arquivos / 3 MB), em qualquer combinação. O `orquestrador.py` executa:

1. **Interpretação** do envio (`rag.interpretacao`) e classificação dos arquivos: circuito QASM,
   circuito Python (Qiskit), Python comum, notebook (células de código extraídas), documento, dados ou ignorado.
2. **Diagnóstico** de todos os circuitos (até `QONE_MAX_CIRCUITOS`, padrão 8), vindos do editor, de
   blocos de código da mensagem ou dos arquivos, no worker do Qiskit (sem LLM, ~1 s cada).
3. **Planejamento** (fila rápida): a LLM gera consultas para a base e para a internet a partir do pedido
   e dos erros do diagnóstico. Só roda com sinal de domínio (termos quânticos, código ou arquivos).
4. **Base local e internet em paralelo aos circuitos.** Nenhuma das duas usa LLM, então rodam enquanto
   a fila corrige os circuitos, sem quebrar a regra de um modelo por vez. Base: busca híbrida com as
   várias consultas, fundidas; trechos viram [Fonte N]. Internet (`pesquisa_web`, padrão ligado): Stack
   Exchange, arXiv e documentação oficial do catálogo verificado; trechos viram [Web N].
5. **Correção e otimização** de cada circuito: correção determinística, conferida de novo pelo Qiskit.
   Se ela for completa e só tiver substituições diretas (nenhuma instrução removida), a LLM **não** é
   chamada. Senão, **correção pela LLM**, revalidada pelo Qiskit (2 tentativas; o motivo de cada recusa
   volta ao modelo). Depois de metade de `QONE_ANALISE_TIMEOUT_SECONDS`, só correções automáticas.
   Em Python legado, as linhas de execução/simulação (Aer, `execute`, `print`,
   `if __name__ == "__main__":`) são esvaziadas e só a construção do circuito é otimizada; nada é executado.
6. **Resposta final** (fila principal, em streaming): o prompt junta pedido, histórico, arquivos,
   resultados do Qiskit, base e internet; a LLM integra as fontes, cita cada uma e evita repetir código.
   Se a fila falhar, resposta local.
7. A conversa é gravada no histórico (`conversation_id`).

Cada etapa e cada modelo consultado são publicados em `rag/progresso.py`; o frontend lê
`GET /analise/progresso/{progress_id}` a cada 0,5 s e mostra o texto ao vivo
(“Redigindo a resposta: consultando nvidia/nemotron-3-ultra-550b-a55b (1/7)”) e, durante a redação,
o texto que o modelo já escreveu (campo `parcial`, em “Escrevendo agora”). Fora do domínio e sem código
nem arquivos, nenhuma LLM é chamada (`insufficient_context`).

O Desktop Chat e o CLI usam as mesmas rotas HTTP. Não há lógica de domínio
no cliente: o frontend apenas exibe o que a API devolve.

## Fluxos

| Fluxo | Caminho |
|---|---|
| Análise unificada | `POST /analise` → orquestrador (interpretação, Qiskit, planejamento, base, internet, fila de LLMs) + `GET /analise/progresso/{id}` |
| Pergunta (rota antiga) | `POST /query` → `rag.interpretacao` → `busca_chromadb` (híbrida) → `avaliar_evidencia` → fila de LLMs NVIDIA **ou** `resposta_local` |
| Código inválido (rota direta) | `POST /qiskit/diagnose` → todos os problemas, correção determinística, validação |
| Circuito válido (rota direta) | `POST /qiskit/optimize` → análise estrutural → candidatos por perfil → verificação → relatório comparativo |
| Conversa | `POST /conversations/{id}/messages` (mesmo pipeline de `/query`, com histórico) |
| CLI | `python qone_cli.py perguntar|buscar|diagnosticar|otimizar|limites|saude` |

## Componentes

### Backend (`main.py`, `servicos.py`, `contratos.py`, `contratos_analise.py`, `orquestrador.py`)
- `orquestrador.py` executa a análise unificada; `contratos_analise.py` define `EntradaAnalise`, `RespostaAnalise` e `ProgressoAnalise`.
- As fontes têm `origin` (`local` ou `web`).
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
| `pipeline.py` | orquestra a consulta; fila de LLMs opcional, fallback local |
| `cliente_llm.py` | fila NVIDIA sequencial (7 modelos, 30 s cada, um de cada vez, streaming, raciocínio desligado) e fila rápida |
| `progresso.py` | estado ao vivo da análise (etapa, modelo em uso, texto parcial, histórico), em memória, TTL 15 min |
| `pesquisa_web.py` | pesquisa na internet sem chave: Stack Exchange, arXiv, documentação oficial (e Wikipedia com `QONE_WEB_CONTATO`) |
| `contexto.py`, `prompts.py` | montagem do contexto (8.000 caracteres) e instruções de análise, correção e planejamento |
| `conhecimento_interno.py` | carrega e indexa `documentos/conhecimento_interno/*.md` |
| `curadoria.py` | deduplicação, boilerplate, classificação e detecção de API obsoleta |
| `coleta_web.py`, `sincronizar_conhecimento.py` | coleta **administrativa** de documentação por allowlist |
| `avaliacao_rag.py` | avaliação reproduzível (Recall@K, MRR, nDCG…) |

Foram removidos o antigo `rag/busca_web.py` (busca web genérica, substituída por `pesquisa_web.py` com fontes fixas) e toda a integração com o Gemini (`cliente_gemini.py`, dependência `google-genai` e variáveis `GEMINI_*`).

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
| `diagnostico.py::extrair_construcao` | esvazia linhas e blocos fora do subconjunto de construção, preservando a numeração |
| `worker.py` | subprocesso descartável com limites de CPU, memória e arquivos (Linux) |

### Frontend (`frontend/`)
- A análise usa só `POST /analise`; `AnalysisLiveStatus` mostra a etapa atual, o **modelo em uso**, o texto sendo escrito (“Escrevendo agora”), os tempos, o histórico e o botão “Cancelar análise”.
- Compositor: “Anexar pasta” (pasta inteira, até 300 arquivos) e “Pesquisar também na internet”.
- Resultado: Markdown seguro, “Redigida por <modelo>”, tentativas dos modelos, cartões por circuito (diagnóstico, correção validada, otimização) e lista de arquivos classificados.
- Removida a ferramenta Terminal e seus estilos.
- `CompactSources`: "Fontes consultadas (N)", recolhido por padrão, com selo “base local” ou “internet”.
- Painel de resultado mostra modo da resposta, motor (perfil, verificação, CX equivalentes, simulação, compromissos) e diagnóstico (problemas, causa, correção, alternativas, código corrigido).
- Seletor de perfil de otimização no compositor.

## Decisões técnicas

1. **Resposta local por padrão sem LLM.** Sem chave (ou se nenhum modelo da fila responder) a API não devolve 503: responde com trechos literais da base. `QONE_LOCAL_FALLBACK=0` restaura o 503 `LLM_NOT_CONFIGURED`.
2. **Base local primeiro, internet como complemento.** A pesquisa web usa só APIs públicas sem chave e páginas da allowlist (com robots.txt), em paralelo e com prazo; os trechos entram separados ([Web N]) e são tratados como dado, nunca como instrução. `QONE_PESQUISA_WEB=0` ou a caixa “Pesquisar também na internet” desligam. `KNOWLEDGE_AUTO_SYNC=0` continua: a base só muda pela coleta administrativa.
7. **Toda correção da LLM passa pelo Qiskit.** Código proposto por um modelo só é apresentado como “validado” depois de carregado pelo carregador restrito no worker.
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
| `NVIDIA_API_KEY` / `NVIDIA_MODELS` / `NVIDIA_MODELS_RAPIDOS` | vazio | fila NVIDIA, ordem dos modelos, fila do planejamento |
| `LLM_TIMEOUT_SECONDS` / `LLM_TEMPO_TOTAL_SECONDS` | `30` / `0` | prazo por modelo, qualquer que seja o prompt; teto da fila (0 = sem teto) |
| `LLM_STREAMING` | `1` | resposta em streaming (texto parcial ao vivo); `0` pede a resposta inteira de uma vez |
| `LLM_MAX_TOKENS` / `LLM_RACIOCINIO` | `16384` / `0` | tokens de saída; `1` religa o raciocínio dos Nemotron |
| `QONE_ANALISE_TIMEOUT_SECONDS` | `900` | teto da análise unificada (API e proxy do frontend) |
| `QONE_MAX_CIRCUITOS` | `8` | circuitos analisados por envio |
| `QONE_PESQUISA_WEB` | `1` | `0` desliga a pesquisa na internet |
| `QONE_WEB_CONTATO` / `STACKEXCHANGE_KEY` | vazio | contato no User-Agent (habilita a Wikipedia) / chave gratuita do Stack Exchange (amplia ~300 consultas/dia) |
| `QONE_WEB_ATRASO_MAXIMO` | `3` | páginas cujo robots.txt pede Crawl-delay maior são puladas na pesquisa ao vivo |
| `QONE_LOCAL_FALLBACK` | `1` | `0` devolve 503 sem LLM |
| `RAG_MODO` | `hibrido` | `vetorial` ou `lexical` |
| `RAG_MIN_COBERTURA` | `0.34` | cobertura mínima de termos para responder |
| `RAG_BONUS_TITULO` | `1.2` | reforço quando o título nomeia o conceito |
| `QONE_LOCAL_CORTE` | `0.7` | fração da melhor pontuação usada na resposta local |
| `QONE_MAX_STATEVECTOR_QUBITS` | `22` | limite do teste por estados aleatórios |
| `QONE_STATEVECTOR_BUDGET` | `6e8` | orçamento amplitudes × portas × estados |
| `QONE_TOL_INFIDELIDADE_APROX` | `1e-8` | limite da equivalência aproximada |
| `KNOWLEDGE_AUTO_SYNC` | `0` | coleta periódica (antes `1`) |

Removidas: `WEB_SEARCH_ENABLED`, `GEMINI_WEB_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`.
Removida também: `LLM_PAUSA_SEGUNDOS` (só erros permanentes pausam: 401/403/404 por 1 h, 429 por 60 s).
Alteradas: `GROWUP_MAX_CONTEXT_CHARS` 12000 → 8000; `LLM_MAX_TOKENS` 16384; `LLM_TEMPO_TOTAL_SECONDS` 0.
