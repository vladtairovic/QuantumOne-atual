# GrowUp — QuantumOne

A versão **Beta-v2.0** transforma o QuantumOne (QOne) em uma plataforma especializada em computação
quântica que **funciona só com a base local**: não há busca web nem consumo de cota durante as
consultas. Ela diagnostica e corrige código OpenQASM e Python/Qiskit danificado, otimiza e verifica
circuitos de até 30 qubits e mede a qualidade do próprio RAG.

| Área | O que mudou | Documento |
|---|---|---|
| Arquitetura | componentes, fluxos, decisões e variáveis | [ARQUITETURA.md](docs/ARQUITETURA.md) |
| Sem busca web | opção removida da interface e do backend; respostas locais quando não há LLM | [ARQUITETURA.md](docs/ARQUITETURA.md#decisões-técnicas) |
| Base de conhecimento | catálogo com 308 fontes verificadas + 11 documentos internos validados | [BASE-DE-CONHECIMENTO.md](docs/BASE-DE-CONHECIMENTO.md) |
| RAG | busca híbrida (vetorial + BM25 + PT→EN), verificação de evidência, avaliação com métricas | [RAG.md](docs/RAG.md) |
| Diagnóstico e motor | todos os erros de uma vez, correções validadas, perfis, verificação até 30 qubits | [MOTOR-QUANTICO.md](docs/MOTOR-QUANTICO.md) |
| Resultados | benchmarks contra a linha de base, problemas resolvidos e limitações | [BENCHMARKS.md](docs/BENCHMARKS.md) |
| Geração de respostas | fila de 7 modelos NVIDIA, 15 s por modelo, fallback local | [LLM.md](docs/LLM.md) |

## Iniciar e abrir

Abra PowerShell nesta pasta:

```powershell
docker compose up --build -d --wait
docker compose ps
```

Frontend: http://localhost:3000

Swagger da API: http://localhost:8000/docs

O primeiro build baixa dependências e o modelo de embeddings. Na inicialização, a API indexa os
exemplos iniciais e o **conhecimento interno** (`documentos/conhecimento_interno/`); depois disso,
consultas não acessam a internet.

A atualização da documentação externa é um **processo administrativo separado**, manual ou em lote,
sem API de busca (detalhes em [BASE-DE-CONHECIMENTO.md](docs/BASE-DE-CONHECIMENTO.md#como-atualizar-processo-administrativo-fora-das-consultas)):

```powershell
docker compose exec api python -m rag.sincronizar_conhecimento
docker compose exec api python -m rag.sincronizar_conhecimento --status
```

## Organização

```mermaid
flowchart LR
 U[Navegador: localhost:3000] --> F[Frontend Next.js]
 T[Terminal: qone_cli.py] --> A
 F -->|/api pela rede Docker| A[Backend FastAPI + RAG + motor quântico]
 A --> C[ChromaDB + BM25 em memória]
 C --> V[(Volume chroma_data)]
 A --> R[Redis: cache de embeddings]
 A --> W[Worker isolado: diagnóstico, otimização, verificação]
 A -.->|opcional, sem busca| G[Gemini]
```

- Frontend: porta 3000; chamadas pela mesma origem, encaminhadas à API interna.
- API: porta local 8000. Os contratos anteriores foram preservados (`web_search` ainda é aceito, mas é ignorado e gera aviso).
- ChromaDB: coleção única `qiskit_documentacao_multilingue`, sem porta publicada no host. Modelo `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, normalizado, 384 dimensões.
- Redis: só na rede interna; cache de vetores com expiração de 1 hora. Se cair, os embeddings são calculados normalmente.
- Volumes `chroma_data`, `model_cache` e `api_data` preservam banco, modelo, avaliações e snapshots.

## Usar a interface

Preencha o código OpenQASM 2/3 ou Python/Qiskit, a pergunta, ou ambos, e clique em Analisar.
- **Código válido**: otimização pelo perfil escolhido (Rápida, Balanceada, Avançada, Experimental), relatório antes/depois, CX equivalentes, método e garantia da verificação, método de simulação recomendado e compromissos entre métricas.
- **Código inválido**: em vez de parar no primeiro erro, a interface mostra o diagnóstico completo (linha, causa, correção, alternativas, confiança) e o código corrigido validado.
- **Pergunta**: RAG híbrido local. Com `GEMINI_API_KEY`, o Gemini redige a resposta a partir dos trechos; sem chave, ou se o Gemini falhar, a resposta é **extrativa local** e identificada como tal.
- **Fontes**: caixa compacta "Fontes consultadas (N)", recolhida por padrão.

Os painéis **Arquivos**, **Diff**, **Qiskit** e **Fontes** continuam disponíveis. A ferramenta Terminal
foi removida da interface; o terminal agora é o CLI abaixo.

## CLI

```powershell
python qone_cli.py saude
python qone_cli.py perguntar "Como medir todos os qubits?"
python qone_cli.py diagnosticar exemplos\circuito_validado_10q.qasm
python qone_cli.py otimizar circuito.qasm --perfil avancado --topologia heavy_hex --portas ibm_heron
```

A URL vem de `--api` ou de `QONE_API_URL` (padrão `http://127.0.0.1:8000`). O CLI usa as mesmas rotas
do Desktop Chat, e nenhum código é executado localmente.

## Contas e e-mail

O cadastro e a alteração de senha usam a API. As credenciais ficam no volume `api_data`. Projetos, histórico, foto e preferências pertencem ao workspace deste navegador. Exporte o backup em **Configurações** para levá-lo a outro navegador.

No Compose local, o Mailpit recebe os códigos de confirmação. [Abrir a caixa de testes](http://localhost:8025). Para entrega em caixas reais, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS=1`, `SMTP_FROM`, `SMTP_USER` e `SMTP_PASSWORD` no ambiente da API.

## Credencial e modelo

A credencial fica somente no `.env` local, ignorado pelo Git e pelos builds. Nunca use chave em variáveis `NEXT_PUBLIC` ou no navegador. Copie `.env.example` para `.env`; as chaves são **opcionais**. Com `NVIDIA_API_KEY`, a resposta é redigida pela fila de modelos NVIDIA ([LLM.md](docs/LLM.md)); sem ela, pelo Gemini, se houver `GEMINI_API_KEY`.

- Sem chave: `/query` responde de forma extrativa com a base local (`answer_mode: "local"`).
- `QONE_LLM=local`: nunca chama a LLM, mesmo com chave (100% offline).
- `QONE_LOCAL_FALLBACK=0`: restaura o comportamento antigo (503 `LLM_NOT_CONFIGURED`).
- Com chave, apenas a pergunta e os trechos locais vão para o Gemini; nenhuma ferramenta de busca é enviada.

Após mudar `.env`: `docker compose up -d --force-recreate api`.

## Parar e retomar

```powershell
docker compose down
docker compose up -d --wait
```

Não use `down -v`, para preservar os dados.

## Testes e evidências

- Backend: `docker build --target test -t growup-api-tests .` e `docker run --rm growup-api-tests` (é o mesmo passo do GitHub Actions).
- Benchmarks e avaliação do RAG: comandos em [BENCHMARKS.md](docs/BENCHMARKS.md#como-reproduzir).
- Integração real em containers: `python scripts/validar_docker.py` (também no CI).
- Navegador: `SKIP_LIVE_LLM=1 node scripts/test_frontend.cjs` (requer Playwright e a stack em execução).

## Circuitos grandes e limites

Os padrões são 2.500 linhas, 32 qubits, 10.000 operações, profundidade 10.000, 512 KiB de código e 60
segundos por análise (`GET /qiskit/limits`). Não existe simulação exata universal de 30 qubits (o
statevector ocupa 16 GiB). O motor escolhe o método de verificação e de simulação pela estrutura do
circuito e diz qual garantia obteve. Detalhes em [MOTOR-QUANTICO.md](docs/MOTOR-QUANTICO.md) e
[circuitos-grandes.md](docs/circuitos-grandes.md).

## Limitações

Ver [BENCHMARKS.md](docs/BENCHMARKS.md#limitações-conhecidas). Em resumo: o catálogo externo só entra
na base após a sincronização administrativa; o PennyLane não é coletado (o site bloqueia coletores); a
resposta local é extrativa; o OpenQASM 3 é convertido no subconjunto básico; circuitos grandes e muito
emaranhados podem ficar sem verificação dentro do orçamento.
