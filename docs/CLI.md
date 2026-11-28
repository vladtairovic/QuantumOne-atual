# QuantumOne CLI (`qone`) · 0.3.0

Assistente de terminal para projetos Python/Qiskit e OpenQASM 2, adaptado da
[QuantumOne CLI independente](https://github.com/dudurfmaia/QuantumOne---CLI) (beta 0.2.2) para o
backend atual do QuantumOne. Você conversa sobre arquivos do seu projeto, consulta as fontes, mede e
corrige circuitos com o Qiskit e revisa alterações antes de gravá-las no disco.

## O que mudou em relação à CLI independente

| Antes (beta 0.2.2) | Agora (0.3.0, neste projeto) |
|---|---|
| Chat por `/conversations` com Gemini e busca web desativada | Chat pela análise unificada (`POST /analise`): base local primeiro, pesquisa na internet como complemento, fila de LLMs NVIDIA (um modelo de cada vez, 45 s cada) e motor Qiskit |
| Indicador “Pensando...” | Texto ao vivo com a etapa e o modelo consultado (“Redigindo a resposta: consultando nvidia/nemotron-3-ultra-550b-a55b (1/7)”), lido de `GET /analise/progresso/{id}` |
| `/propor` pedia JSON à rota `/query` (falhava sem evidência na base) | `/propor` usa `POST /assistente/proposta`, feita para propostas estruturadas; a versão proposta dos circuitos é conferida pelo Qiskit antes de `/aplicar` |
| `/qiskit` no perfil padrão | `/qiskit` no perfil `avancado` (o mais forte que segue exigindo verificação de equivalência, igual à interface), com CX equivalentes, verificação e simulação recomendada |
| — | `/diagnosticar`: todos os problemas do arquivo aberto; a correção validada pelo Qiskit vira uma proposta para `/aplicar` |
| Respostas sem origem | “Redigida por <modelo>”, resumo dos circuitos e das fontes; `/fontes` separa `[Fonte N]` (base local) de `[Web N]` (internet) |
| Bloqueio de credenciais (GitHub, Google) | Também bloqueia chaves NVIDIA (`nvapi-...`) antes de enviar qualquer conteúdo |

O histórico continua local (`~/.quantumone/cli.sqlite3`, ou `QONE_STATE_DIR`), assim como as
edições, que podem ser desfeitas com `/desfazer`.

## Iniciar

Abra o terminal na raiz do projeto (onde estão `compose.yaml`, `cli.py`, `qone_core.py` e
`qone_store.py`). Use Python 3.10 ou superior e deixe o Docker Desktop aberto.

1. Configure `NVIDIA_API_KEY` no `.env` (veja `.env.example`) para o chat e as propostas. Sem chave, o
   chat responde de forma extrativa com a base local e `/propor` avisa que precisa da fila de LLMs;
   `/qiskit` e `/diagnosticar` funcionam sem chave.
2. Inicie a API e confira:

   ```powershell
   docker compose up -d --build --wait api
   curl.exe -fsS http://localhost:8000/health
   ```

3. Abra o chat:

   ```powershell
   python cli.py --repo . --chat
   ```

A URL da API vem de `--api` ou de `QONE_API_URL` (padrão `http://localhost:8000`).

## Instalar o comando `qone`

```powershell
python -m venv .venv-cli
.venv-cli\Scripts\Activate.ps1
python -m pip install .
qone --repo . --chat
```

No Git Bash, ative com `source .venv-cli/Scripts/activate`. Confira a versão com `qone --version`.

## Primeira análise

Quando aparecer **Você >**, digite uma linha por vez:

```text
/arquivos
/abrir exemplos/circuito_validado_10q.qasm
Quantos qubits estão declarados e quais são as três primeiras portas?
/fontes
/qiskit
/diagnosticar
```

A pergunta vai para a análise unificada com o arquivo aberto (reenviado só quando muda) e o histórico
da conversa. Enquanto a análise roda, a linha de status mostra a etapa e o modelo em uso.

## Revisar uma alteração

Com um arquivo aberto, use `/propor` seguido do pedido. Leia o plano, o diff e a conferência do Qiskit.
Digite `/aplicar` para gravar ou `/cancelar` para descartar; `/desfazer` restaura a última edição da
CLI. Para rodar os testes do projeto, use `/testar unittest` ou `/testar pytest`: a execução pede
autorização e usa as permissões da sua conta. Depois de um teste falhar, “corrija os testes” monta o
contexto (teste + módulos importados) e pede a proposta com a saída do teste.

## Comandos

| Comando | Função |
|---|---|
| `/help` | Lista os comandos. |
| `/status` | Projeto, API (com o estado de `/health`), arquivos abertos, conversa e proposta pendente. |
| `/abrir`, `/anexar` | Seleciona arquivos `.py`/`.qasm` do workspace para a conversa e as propostas. |
| `/arquivos`, `/mapa`, `/buscar` | Lista, indexa (símbolos e imports) e procura texto no projeto. |
| `/propor`, `/diff`, `/aplicar`, `/cancelar`, `/desfazer` | Propostas de edição revisadas antes de gravar. |
| `/criar`, `/excluir`, `/mover` | Operações de arquivo, também revisadas antes de gravar. |
| `/qiskit [0-3]` | Otimização verificada do primeiro arquivo aberto (perfil `avancado`). |
| `/diagnosticar` | Diagnóstico completo; a correção validada vira proposta. |
| `/testar`, `/git` | Testes locais e Git (status, branches, diff, commit local). |
| `/history`, `/retomar ID`, `/esquecer ID` | Conversas salvas neste workspace. |
| `/fontes`, `/model`, `/eventos`, `/multi`, `/clear`, `/sair` | Utilidades da conversa. |

No modo script (`--roteiro arquivo.txt` ou pergunta na linha de comando), efeitos locais exigem
`--permitir` (`editar`, `testes`, `workspace`, `historico`, `commit`, `clonar`). Exemplo em uma linha:

```powershell
python cli.py --repo . exemplos\circuito_validado_10q.qasm "/qiskit 1"
```

No Git Bash, use `MSYS2_ARG_CONV_EXCL='*'` antes do comando para que `/qiskit` não vire um caminho.

## Rota usada pelas propostas

`POST /assistente/proposta` recebe `{pedido, arquivos: {caminho: "1: linha\n2: ..."}, resultado_testes,
progress_id}` e devolve `{response, llm, sources}`, em que `response` é o JSON da proposta
(`plano`, `riscos`, `verificacao`, `arquivos` com intervalos de linhas). Ela consulta a base local como
referência e usa a fila de LLMs; nada é gravado no servidor. A CLI valida a resposta, mostra o diff e
só grava com `/aplicar`.

## CLI de comandos avulsos

`qone_cli.py` continua disponível para comandos de uma linha (`saude`, `perguntar`, `analisar`,
`diagnosticar`, `otimizar`, `limites`), úteis em scripts. A CLI interativa acima é a que tem chat,
workspace, propostas e histórico.
