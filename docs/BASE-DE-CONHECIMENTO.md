# Base de conhecimento do QuantumOne

A base tem duas camadas, sempre identificadas nos metadados e nas respostas:

| Camada | Origem | Identificação |
|---|---|---|
| Documentação externa | catálogo de fontes públicas, coletado administrativamente | `origem_conhecimento=documentacao_externa`, URL da fonte |
| Conhecimento interno | textos originais do projeto, validados por teste | `origem_conhecimento=qone_interno`, `qone://conhecimento/<id>` |

## Catálogo de fontes externas

`documentos/fontes_conhecimento.json` é gerado por `scripts/catalogo_fontes.py`. Das 359 URLs
candidatas, **308 foram baixadas e extraídas com sucesso** (≈3,6 milhões de caracteres) em
23/11/2026 e permaneceram no catálogo. O relatório completo, inclusive as rejeitadas, está em
`docs/catalogo-verificacao.json`.

| Tipo | Fontes | | Biblioteca | Fontes |
|---|---|---|---|---|
| documentação oficial | 212 | | qiskit (SDK, Runtime, addons, learning) | 187 |
| artigos acadêmicos (arXiv) | 27 | | geral (arXiv, Algorithm Zoo) | 28 |
| tutoriais oficiais | 24 | | openqasm | 16 |
| material educacional (cursos IBM) | 18 | | cirq | 14 |
| especificação oficial (OpenQASM 3) | 16 | | qsharp (Azure Quantum) | 14 |
| documentação comunitária (qiskit-community) | 10 | | qiskit_aer | 13 |
| catálogo acadêmico (Quantum Algorithm Zoo) | 1 | | braket / qutip / python / numpy / scipy | 9 / 8 / 8 / 6 / 5 |

São 107 temas classificados (os mais frequentes: simulação, transpilação, OpenQASM 3, circuitos,
otimização, portas, ruído, algoritmos, roteamento, hardware, mitigação, variacional) e três níveis
(básico 55, intermediário 94, avançado 159).

Rejeitadas (51): 19 respostas 404 (páginas que não existem mais), 10 respostas 403 e 22 recusas por
regra (redirecionamento para fora da allowlist ou página sem conteúdo extraível). **O PennyLane ficou
fora**: `docs.pennylane.ai` responde 403 ao `robots.txt` para coletores e as demos em `pennylane.ai`
redirecionam para fora da allowlist. A coleta respeita essa decisão do site.

### Licenças e redistribuição
Cada fonte tem o campo `licenca`. Os snapshots coletados ficam no volume de dados da API
(`KNOWLEDGE_PATH`) e **não são versionados no repositório**. Do arXiv só são coletados a página de
resumo e os metadados. Antes de redistribuir snapshots, confira os termos de cada site.

## Pipeline de ingestão (`rag/sincronizar_conhecimento.py`)

1. **Aquisição autorizada**: allowlist de domínios e prefixos (`rag/coleta_web.py`), `robots.txt` e
   `crawl-delay` por site, sem query string, sem credenciais, sem seguir redirecionamentos para fora
   da allowlist, limite de 8 MiB por página.
2. **Extração e normalização**: conteúdo principal (`main`, `article`, `[role=main]`…), remoção de
   navegação, rodapés e scripts; código em `<pre>` preservado como bloco `codigo`.
3. **Deduplicação**: blocos repetidos no documento e boilerplate presente em 3+ documentos
   (`rag/curadoria.py`).
4. **Segmentação**: chunks por seção dentro do limite de tokens do modelo, sem cortar frases ou
   identificadores (`rag/preparar_chunks.py`).
5. **Classificação**: biblioteca, temas, nível, tipo de fonte, licença e idioma nos metadados de cada chunk.
6. **Versões e datas**: URL final, URL solicitada, versão declarada e data de coleta.
7. **Indexação**: embeddings + ChromaDB; o BM25 é reconstruído automaticamente após gravações.
8. **Informação desatualizada**: as regras de `quantum/migracao.py` marcam `desatualizado=true`
   quando a página recomenda APIs removidas do Qiskit (guias de migração e outras bibliotecas são
   isentos). Exemplos reais detectados na verificação: tutoriais do Aer com `circ.u1`,
   `from qiskit import Aer` e `IBMQ`.
9. **Atualização incremental**: hash por documento; páginas inalteradas não são reindexadas, e um
   snapshot anterior é guardado (`*.previous.json`). `--reindex` reconstrói tudo sem internet.

### Como atualizar (processo administrativo, fora das consultas)
```bash
# Auditar as URLs do catálogo sem indexar:
docker compose exec api python -m rag.sincronizar_conhecimento --verificar-catalogo /app/data/verificacao.json
# Coletar e indexar o catálogo inteiro (incremental):
docker compose exec api python -m rag.sincronizar_conhecimento
# Reconstruir o índice só com os snapshots locais (offline):
docker compose exec api python -m rag.sincronizar_conhecimento --reindex
```
`KNOWLEDGE_AUTO_SYNC=1` reativa a coleta periódica; o padrão é `0`. Nenhum desses comandos usa API
de busca ou cota de terceiros.

## Conhecimento interno (`documentos/conhecimento_interno/`)

Onze documentos originais, em português, com termos técnicos em inglês para a busca:

| Documento | Conteúdo |
|---|---|
| 01 Fundamentos matemáticos | números complexos, Dirac, produto tensorial, unitárias, observáveis |
| 02 Portas e identidades | identidades usadas por otimizadores, custo de 2 qubits, Toffoli = 6 CX |
| 03 Migração para Qiskit 2.x | APIs removidas e substitutos (execute, Aer, cnot, u3, c_if, pulse…) |
| 04 OpenQASM 2 e 3 | estrutura, diferenças e erros comuns |
| 05 Erros e depuração | método de depuração, ImportError, AttributeError, índices, medições |
| 06 Otimização | estágios do transpilador, otimizações locais, roteamento, compromissos |
| 07 Simulação e limites | memória do statevector até 30 qubits, Clifford, MPS, shots |
| 08 Equivalência | até fase global, métodos por tamanho, tipos de veredito |
| 09 Algoritmos | Bell/GHZ, Bernstein–Vazirani, Grover, QFT, QPE, VQE/QAOA |
| 10 Correção e mitigação | QEC vs mitigação, código de repetição, surface code, ruído |
| 11 Ordem de bits e medição | little-endian, registradores, medições intermediárias |

**Validação antes de entrar na base**: os 26 blocos marcados ` ```python validar ` / ` ```qasm validar `
são executados (Python em subprocesso isolado) ou analisados contra o Qiskit instalado em
`tests/test_conhecimento_interno.py`. Todas as 27 regras de migração são conferidas no Qiskit 2.6
(a API antiga não existe e o substituto existe). A carga na API nunca executa código; documentos
inalterados não são reindexados (hash de conteúdo).
