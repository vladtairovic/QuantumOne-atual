"""Análise unificada (POST /analise): texto, código e pastas, com Qiskit real e LLM/internet simuladas."""
import json
import time
from uuid import uuid4

import httpx
import pytest

import orquestrador
from rag import cliente_llm, pesquisa_web, progresso
from tests.test_integracao import contexto  # noqa: F401  (fixture)

QASM_QUEBRADO = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[9];\nmeasure q -> c;\n'
QASM_CORRIGIDO = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q -> c;\n'
NOTEBOOK = json.dumps({"cells": [{"cell_type": "markdown", "source": ["# Bell"]},
                                 {"cell_type": "code", "source": ["from qiskit import QuantumCircuit\n", "qc = QuantumCircuit(2)\n",
                                                                  "qc.h(0)\n", "qc.cnot(0, 1)\n"]}]})


@pytest.fixture
def llm_simulada(monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("CONVERSAS_PATH", str(tmp_path / "conversas.sqlite"))
    chamadas = []

    def conversar(mensagens, modelos=None, tarefa="Resposta", ao_progresso=None, transporte=None, temperatura=0.2,
                  max_tokens=None, ao_parcial=None, **_):
        chamadas.append({"tarefa": tarefa, "prompt": mensagens[-1]["content"]})
        if ao_progresso:
            ao_progresso("nvidia/modelo-teste", 1, 7, tarefa)
        if tarefa.startswith("Planejando"):
            texto = json.dumps({"objetivo": "corrigir o circuito", "consultas_base": ["CNOT medição Qiskit"],
                                "consultas_web": ["qiskit cx index out of range"], "precisa_web": True})
        elif tarefa.startswith("Correção"):
            texto = ("```qasm\n" + QASM_CORRIGIDO + "```\nTroquei q[9] por q[1]: o registrador tem 2 qubits."
                     if "OPENQASM" in mensagens[-1]["content"] else
                     "```python\nfrom qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n```\ncnot -> cx.")
        else:
            texto = "Resposta final combinando [Fonte 1] e [Web 1]."
        return {"texto": texto, "modelo": "nvidia/modelo-teste",
                "tentativas": [{"modelo": "nvidia/modelo-teste", "resultado": "ok", "segundos": 0.1}]}

    monkeypatch.setattr(cliente_llm, "conversar", conversar)
    monkeypatch.setattr(pesquisa_web, "pesquisar", lambda consultas, **k: (
        [{"fonte": "stackexchange", "titulo": "Index out of range", "url": "https://quantumcomputing.stackexchange.com/q/1",
          "trecho": "Use um índice menor que o tamanho do registrador."}],
        {"consultas": consultas, "fontes": {"stackexchange": 1}, "falhas": {}, "segundos": 0.1}))
    return chamadas


def _documento(client):
    texto = "Medição de qubits no Qiskit. Use QuantumCircuit.measure e a porta CNOT (cx) entre dois qubits do circuito."
    assert client.post("/documents", files={"file": ("medicao.txt", texto.encode())}).status_code == 201


def test_classifica_arquivos_de_uma_pasta():
    classificados, circuitos, documentos = orquestrador.classificar_arquivos([
        {"nome": "proj/a.qasm", "conteudo": QASM_QUEBRADO}, {"nome": "proj/b.py", "conteudo": "import qiskit\nqc=1"},
        {"nome": "proj/util.py", "conteudo": "def soma(a, b):\n    return a + b"},
        {"nome": "proj/bell.ipynb", "conteudo": NOTEBOOK}, {"nome": "proj/README.md", "conteudo": "# Projeto"},
        {"nome": "proj/foto.png", "conteudo": "binario"}, {"nome": "proj/vazio.txt", "conteudo": "  "}])
    tipos = {c["nome"]: c["tipo"] for c in classificados}
    assert tipos == {"proj/a.qasm": "circuito_qasm", "proj/b.py": "circuito_python", "proj/util.py": "python",
                     "proj/bell.ipynb": "notebook", "proj/README.md": "documento", "proj/foto.png": "ignorado",
                     "proj/vazio.txt": "ignorado"}
    assert [c["origem"] for c in circuitos] == ["proj/a.qasm", "proj/b.py", "proj/bell.ipynb"]
    assert "qc.cnot(0, 1)" in circuitos[2]["codigo"]
    assert {d["nome"] for d in documentos} == {"proj/util.py", "proj/README.md"}


def test_codigo_na_mensagem_so_quando_e_programa():
    assert orquestrador.circuitos_da_mensagem("Como usar qc.measure_all() no Qiskit?") == []
    bloco = orquestrador.circuitos_da_mensagem("Corrija:\n```qasm\n" + QASM_QUEBRADO + "```")
    assert bloco and bloco[0]["formato"] == "qasm" and "q[9]" in bloco[0]["codigo"]


def test_analise_completa_texto_codigo_e_pasta(contexto, llm_simulada):
    client, _ = contexto
    _documento(client)
    pid = str(uuid4())
    corpo = {"mensagem": "Corrija meu circuito de medição com CNOT e explique", "codigo": QASM_QUEBRADO, "formato": "qasm",
             "arquivos": [{"nome": "proj/bell.ipynb", "conteudo": NOTEBOOK}, {"nome": "proj/README.md", "conteudo": "Circuito Bell."}],
             "progress_id": pid, "perfil": "rapido"}
    r = client.post("/analise", json=corpo)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["answer_mode"] == "llm" and d["llm"]["modelo"] == "nvidia/modelo-teste"
    assert d["resposta"].startswith("Resposta final")
    editor = d["circuitos"][0]
    assert editor["origem"] == "editor" and editor["valido_original"] is False and editor["problemas"] >= 1
    assert editor["correcao"]["metodo"] == "llm" and editor["correcao"]["valido"] is True
    assert "cx q[0],q[1];" in editor["correcao"]["codigo"]
    assert editor["otimizacao"]["verificacao"]["metodo"] in ("operador", "identidade", "clifford")
    notebook = d["circuitos"][1]
    assert notebook["origem"] == "proj/bell.ipynb" and notebook["correcao"]["metodo"] in ("llm", "deterministica")
    assert {s["origin"] for s in d["sources"]} == {"local", "web"}
    tarefas = [c["tarefa"] for c in llm_simulada]
    # Ordem: diagnóstico (sem LLM) -> planejamento -> correções -> resposta; uma chamada de LLM por vez.
    assert tarefas[0].startswith("Planejando") and tarefas[1].startswith("Correção de editor")
    assert tarefas[-1] == "Redigindo a resposta"
    # O planejamento já recebeu os erros do diagnóstico do Qiskit.
    assert "INDEX_OUT_OF_RANGE" in llm_simulada[0]["prompt"]
    # A resposta final recebeu Qiskit, base local, internet e o conteúdo dos arquivos.
    final = llm_simulada[-1]["prompt"]
    assert "<resultados_qiskit>" in final and "[Fonte 1]" in final and "[Web 1]" in final and "proj/README.md" in final
    p = client.get(f"/analise/progresso/{pid}").json()
    assert p["concluido"] is True
    assert any("consultando nvidia/modelo-teste" in e["texto"] for e in p["historico"])
    # A busca rodou em paralelo aos circuitos e ficou registrada no histórico.
    assert any("em paralelo aos circuitos" in e["texto"] for e in d["etapas"])
    assert any(e["texto"].startswith("Base local:") for e in d["etapas"])
    assert any(e["texto"].startswith("Internet: 1 resultado") for e in d["etapas"])
    assert d["conversation_id"]


def test_pergunta_fora_do_dominio_nao_chama_llm(contexto, llm_simulada):
    client, _ = contexto
    d = client.post("/analise", json={"mensagem": "Qual a melhor receita de lasanha?"}).json()
    assert d["status"] == "insufficient_context" and d["answer_mode"] == "none"
    assert llm_simulada == []


def test_falha_da_fila_vira_resposta_local_com_resultados_do_qiskit(contexto, monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    monkeypatch.setenv("CONVERSAS_PATH", str(tmp_path / "c.sqlite"))
    def falhar(*a, **k):
        raise cliente_llm.FilaEsgotada([{"modelo": "m", "resultado": "tempo_esgotado_15s", "segundos": 15.0}])
    monkeypatch.setattr(cliente_llm, "conversar", falhar)
    monkeypatch.setattr(pesquisa_web, "pesquisar", lambda c, **k: ([], {"consultas": c, "fontes": {}, "falhas": {}}))
    monkeypatch.setattr(orquestrador, "TEMPO_ANALISE", 36)  # prazo curto: insiste até o fim do prazo e cai no local
    client, _ = contexto
    d = client.post("/analise", json={"mensagem": "Otimize este circuito", "codigo": QASM_CORRIGIDO}).json()
    assert d["answer_mode"] == "local" and "Resultados do Qiskit" in d["resposta"]
    assert any("rodada(s) da fila" in a for a in d["warnings"])
    assert d["circuitos"][0]["valido_original"] is True and d["circuitos"][0]["otimizacao"]


def test_envio_vazio_e_recusado(contexto):
    client, _ = contexto
    assert client.post("/analise", json={"mensagem": "  "}).status_code == 422


def test_fila_sequencial_cancela_e_passa_ao_proximo(monkeypatch):
    """Um modelo de cada vez: o segundo só começa depois de o primeiro estourar o prazo."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.4")
    cliente_llm.limpar_pausas()
    inicios, anunciados = {}, []

    def responder(request):
        modelo = json.loads(request.content)["model"]
        inicios[modelo] = time.monotonic()
        if modelo == "lento":
            time.sleep(1.5)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok de " + modelo}}]})

    t0 = time.monotonic()
    r = cliente_llm.conversar([{"role": "user", "content": "x"}], ["lento", "rapido"], "Teste",
                              lambda m, p, t, tarefa: anunciados.append((m, p, t)), httpx.MockTransport(responder))
    assert r["modelo"] == "rapido" and anunciados == [("lento", 1, 2), ("rapido", 2, 2)]
    # o segundo esperou o prazo inteiro do primeiro (folga: o relógio do Windows avança de ~15,6 ms em ~15,6 ms)
    assert inicios["rapido"] - t0 >= 0.4 - 0.03
    assert r["tentativas"][0]["resultado"].startswith("tempo_esgotado")


def test_tempo_esgotado_nao_pausa_mas_404_pausa(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    cliente_llm.limpar_pausas()
    def responder(request):
        modelo = json.loads(request.content)["model"]
        return httpx.Response(404 if modelo == "sumido" else 200, json={"choices": [{"message": {"content": "ok"}}]})
    t = httpx.MockTransport(responder)
    cliente_llm.conversar([{"role": "user", "content": "x"}], ["sumido", "bom"], transporte=t)
    r = cliente_llm.conversar([{"role": "user", "content": "x"}], ["sumido", "bom"], transporte=t)
    assert r["tentativas"][0]["resultado"] == "pausado"
    cliente_llm.limpar_pausas()


def test_raciocinio_desligado_so_nos_nemotron(monkeypatch):
    monkeypatch.delenv("LLM_RACIOCINIO", raising=False)
    assert cliente_llm._corpo("nvidia/nemotron-3-ultra-550b-a55b", [], 10, 0.2)["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in cliente_llm._corpo("poolside/laguna-xs-2.1", [], 10, 0.2)
    assert cliente_llm._corpo("x", [], None or 16384, 0.2)["max_tokens"] == 16384


def test_progresso_inexistente_retorna_404(contexto):
    client, _ = contexto
    assert client.get(f"/analise/progresso/{uuid4()}").status_code == 404
    rastreador = progresso.Rastreador(uuid4())
    rastreador.modelo("nvidia/x", 2, 7, "Redigindo a resposta")
    estado = progresso.obter(rastreador.pid)
    assert estado["modelo"] == "nvidia/x" and "consultando nvidia/x (2/7)" in estado["etapa"]


def test_cli_analisa_pasta_com_progresso(contexto, llm_simulada, tmp_path):
    import io

    from qone_cli import executar
    client, _ = contexto
    _documento(client)
    pasta = tmp_path / "proj"
    (pasta / "node_modules").mkdir(parents=True)
    (pasta / "bell.qasm").write_text(QASM_QUEBRADO, encoding="utf-8")
    (pasta / "README.md").write_text("# Projeto Bell", encoding="utf-8")
    (pasta / "node_modules" / "lixo.js").write_text("x", encoding="utf-8")
    saida = io.StringIO()
    codigo = executar(["analisar", "Corrija o circuito de Bell e explique a medição", "--anexo", str(pasta)],
                      cliente=client, saida=saida)
    texto = saida.getvalue()
    assert codigo == 0, texto
    assert "Resposta final combinando" in texto and "[Redigida por nvidia/modelo-teste]" in texto
    assert "Circuito proj/bell.qasm" in texto and "validada pelo Qiskit" in texto and "cx q[0],q[1];" in texto
    assert "1 circuito_qasm" in texto and "1 documento" in texto and "lixo.js" not in texto
    assert "[Web 1]" in texto and "[Fonte 1]" in texto


GHZ_LEGADO = '''from qiskit import QuantumCircuit, Aer, execute

qc = QuantumCircuit(3, 3)
qc.h(0)
qc.cnot(0, 1)
qc.cnot(1, 2)
qc.measure([0, 1, 2], [0, 1, 2])

if __name__ == "__main__":
    backend = Aer.get_backend("qasm_simulator")
    print(execute(qc, backend, shots=1024).result().get_counts())
'''


def test_correcao_automatica_reescreve_execute_aninhado_e_passa_na_reverificacao():
    from quantum.diagnostico import diagnosticar
    diag = diagnosticar(GHZ_LEGADO, "python")
    corrigido = diag["codigo_corrigido"]
    assert "execute(" not in corrigido and "backend.run(transpile(qc, backend), shots=1024)" in corrigido
    assert "from qiskit import transpile" in corrigido and "AerSimulator()" in corrigido
    reverificacao = diagnosticar(corrigido, "python")
    assert reverificacao["validacao"]["valido"]
    assert not [p for p in reverificacao["problemas"] if p["severidade"] == "erro"]


def test_regras_de_api_removida_ignoram_comentarios_e_strings():
    from quantum.diagnostico import diagnosticar
    codigo = ('from qiskit import QuantumCircuit, transpile\nfrom qiskit_aer import AerSimulator\n'
              '# execute(qc, backend) foi removido; usamos backend.run\nqc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n'
              'aviso = "não use execute(qc, b)"\nnome = f"execute({qc.name})"\n')
    diag = diagnosticar(codigo, "python")
    assert not [p for p in diag["problemas"] if p["severidade"] == "erro"]
    assert diag["validacao"]["valido"]


def test_extracao_mantem_construtores_de_import_misto():
    from quantum.diagnostico import extrair_construcao
    codigo, ignoradas, circuito = extrair_construcao(
        "from qiskit import QuantumCircuit, transpile\nqc = QuantumCircuit(2)\nqc.h(0)\nqc.cx(0, 1)\n")
    assert circuito.num_qubits == 2 and codigo.splitlines()[0] == "from qiskit import QuantumCircuit"
    assert ignoradas == [{"linha": 1, "texto": "import de transpile", "motivo": ignoradas[0]["motivo"]}]


def test_correcao_automatica_completa_dispensa_a_llm(contexto, llm_simulada):
    client, _ = contexto
    d = client.post("/analise", json={"mensagem": "Meu projeto GHZ não roda no Qiskit 2. Corrija.", "codigo": GHZ_LEGADO,
                                      "formato": "python", "pesquisa_web": False}).json()
    circuito = d["circuitos"][0]
    assert circuito["correcao"]["metodo"] == "deterministica" and circuito["correcao"]["valido"] is True
    assert "nenhuma instrução removida" in circuito["correcao"]["explicacao"]
    assert not [c for c in llm_simulada if c["tarefa"].startswith("Correção")]
    assert any("a LLM não foi necessária" in e["texto"] for e in d["etapas"])
    assert circuito["otimizacao"] and circuito["otimizacao"]["equivalente"] is True


def test_proposta_da_cli_usa_a_fila_e_devolve_json(contexto, monkeypatch):
    client, _ = contexto
    _documento(client)
    monkeypatch.setenv("NVIDIA_API_KEY", "chave-de-teste")
    recebido = {}

    def conversar(mensagens, modelos=None, tarefa="Resposta", ao_progresso=None, transporte=None, temperatura=0.2,
                  max_tokens=None, ao_parcial=None, **_):
        recebido.update(sistema=mensagens[0]["content"], dados=json.loads(mensagens[1]["content"]), tarefa=tarefa)
        if ao_progresso:
            ao_progresso("nvidia/modelo-teste", 1, 7, tarefa)
        proposta = {"plano": "trocar cnot", "riscos": "nenhum", "verificacao": "Qiskit",
                    "arquivos": {"bell.qasm": [{"inicio": 2, "fim": 2, "linhas": ["cx q[0],q[1];"]}]}}
        return {"texto": json.dumps(proposta), "modelo": "nvidia/modelo-teste",
                "tentativas": [{"modelo": "nvidia/modelo-teste", "resultado": "ok", "segundos": 0.1}]}

    monkeypatch.setattr(cliente_llm, "conversar", conversar)
    pid = str(uuid4())
    r = client.post("/assistente/proposta", json={"pedido": "corrija a porta", "progress_id": pid,
                                                  "arquivos": {"bell.qasm": "1: h q[0];\n2: cnot q[0],q[1];"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert json.loads(d["response"])["arquivos"]["bell.qasm"][0]["linhas"] == ["cx q[0],q[1];"]
    assert d["llm"]["modelo"] == "nvidia/modelo-teste"
    assert recebido["tarefa"] == "Preparando a proposta" and "SOMENTE com um objeto JSON" in recebido["sistema"]
    assert recebido["dados"]["arquivos"] == {"bell.qasm": "1: h q[0];\n2: cnot q[0],q[1];"}
    p = client.get(f"/analise/progresso/{pid}").json()
    assert p["concluido"] is True and any("consultando nvidia/modelo-teste" in e["texto"] for e in p["historico"])


def test_proposta_da_cli_sem_llm_explica_e_valida_entrada(contexto, monkeypatch):
    client, _ = contexto
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    r = client.post("/assistente/proposta", json={"pedido": "x", "arquivos": {"a.py": "1: pass"}})
    assert r.status_code == 503 and r.json()["code"] == "LLM_NOT_CONFIGURED"
    assert client.post("/assistente/proposta", json={"pedido": "x", "arquivos": {}}).status_code == 422


def test_otimizar_python_de_projeto_extrai_so_a_construcao(contexto):
    client, _ = contexto
    codigo = ("from qiskit import QuantumCircuit, transpile\nfrom qiskit_aer import AerSimulator\n\n"
              "qc = QuantumCircuit(2, 2)\nqc.h(0)\nqc.cx(0, 1)\nqc.measure([0, 1], [0, 1])\n\n"
              "if __name__ == \"__main__\":\n    backend = AerSimulator()\n"
              "    print(backend.run(transpile(qc, backend), shots=100).result().get_counts())\n")
    corpo = {"code": codigo, "format": "python", "perfil": "avancado"}
    assert client.post("/qiskit/optimize", json=corpo).status_code == 422  # rota estrita por padrão
    r = client.post("/qiskit/optimize", json={**corpo, "extrair_construcao": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["original"]["qubits"] == 2 and d["equivalente"] is True
    assert any("ignorad" in w.lower() for w in d["warnings"])


def test_perfil_local_orienta_o_nivel_da_resposta(contexto, llm_simulada):
    from rag.prompts import INSTRUCOES, INSTRUCOES_ANALISE
    client, _ = contexto
    _documento(client)
    corpo = {"mensagem": "O que é uma porta CNOT e para que serve a medição de qubits?", "pesquisa_web": False}
    r = client.post("/analise", json={**corpo, "usuario": {"experiencia": "iniciante", "estilo": "didatica",
                                                           "papel": "estudante"}})
    assert r.status_code == 200, r.text
    final = llm_simulada[-1]["prompt"]
    assert "<perfil_do_usuario>" in final and "iniciante" in final and "didática" in final and "estudante" in final
    llm_simulada.clear()
    assert client.post("/analise", json=corpo).status_code == 200
    assert "<perfil_do_usuario>" not in llm_simulada[-1]["prompt"]  # sem perfil: nível inferido pela pergunta
    assert client.post("/analise", json={**corpo, "usuario": {"experiencia": "guru"}}).status_code == 422
    for instrucoes in (INSTRUCOES_ANALISE, INSTRUCOES):
        assert "Leigo ou iniciante" in instrucoes and "Experiente ou avançado" in instrucoes
        assert "300 a 700" not in instrucoes and "Nunca entregue uma resposta rasa" in instrucoes


def test_verificacao_na_analise_so_informa(contexto, llm_simulada, monkeypatch):
    from rag import verificacao
    client, _ = contexto
    _documento(client)
    corpo = {"mensagem": "Como medir qubits com CNOT no Qiskit?", "pesquisa_web": False}
    monkeypatch.setenv("RAG_VERIFICACAO", "0")
    assert client.post("/analise", json=corpo).json()["verificacao"] is None
    monkeypatch.setenv("RAG_VERIFICACAO", "1")
    monkeypatch.setattr(verificacao, "verificar_resposta", lambda resposta, fontes: {
        "fidelidade": 0.25, "texto": resposta,
        "frases": [{"suportada": False}, {"suportada": False}, {"suportada": False}, {"suportada": True}]})
    d = client.post("/analise", json=corpo).json()
    assert d["verificacao"] == {"fidelidade": 0.25, "frases_avaliadas": 4, "frases_sem_suporte": 3}
    assert d["answer_mode"] == "llm" and d["resposta"].startswith("Resposta final")  # não descartou
    assert any(e["texto"].startswith("Verificação: 25%") for e in d["etapas"])


def test_resposta_da_llm_sai_sem_marcadores_de_fonte(contexto, llm_simulada):
    from rag.prompts import INSTRUCOES, INSTRUCOES_ANALISE
    from rag.texto import remover_marcadores_de_fonte
    client, _ = contexto
    _documento(client)
    d = client.post("/analise", json={"mensagem": "Como medir qubits com CNOT no Qiskit?", "pesquisa_web": False}).json()
    # a LLM simulada escreve "[Fonte 1] e [Web 1]"; quem lê recebe o texto limpo, e as fontes seguem em sources
    assert "[Fonte" not in d["resposta"] and "[Web" not in d["resposta"] and d["sources"]
    assert "[Fonte 1]" in llm_simulada[-1]["prompt"]  # os trechos continuam numerados no contexto da LLM
    for instrucoes in (INSTRUCOES_ANALISE, INSTRUCOES):
        assert "Não escreva marcadores de fonte" in instrucoes and "Cite" not in instrucoes
    assert remover_marcadores_de_fonte("Use cx [Fonte 2][Web 1] e qc[0] [Fonte 1, Fonte 3].") == "Use cx e qc[0]."


def test_analise_que_falha_e_refeita_em_vez_de_dar_erro(contexto, llm_simulada, monkeypatch):
    import main
    client, _ = contexto
    _documento(client)
    original, chamadas = orquestrador.analisar, {"n": 0}

    async def instavel(entrada, request_id):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("falha inesperada")
        return await original(entrada, request_id)

    monkeypatch.setattr(orquestrador, "analisar", instavel)
    monkeypatch.setattr(main, "_pausa_antes_de_refazer", original_sleep)
    r = client.post("/analise", json={"mensagem": "Como medir qubits com CNOT no Qiskit?", "pesquisa_web": False})
    assert r.status_code == 200 and chamadas["n"] == 2
    d = r.json()
    assert d["answer_mode"] == "llm" and d["warnings"][0].startswith("A análise falhou 1 vez(es) (RuntimeError)")


async def original_sleep(segundos):
    return None


def test_analise_sem_tempo_responde_sem_erro(contexto, monkeypatch):
    import main
    client, _ = contexto

    async def sempre_falha(entrada, request_id):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(orquestrador, "analisar", sempre_falha)
    monkeypatch.setattr(main, "TEMPO_ANALISE", 100)  # menos que a margem para refazer
    r = client.post("/analise", json={"mensagem": "Como medir qubits?"})
    assert r.status_code == 200  # nunca um erro para o usuário
    d = r.json()
    assert d["answer_mode"] == "none" and "envie de novo" in d["resposta"] and "RuntimeError" in d["warnings"][0]


def test_worker_ocupado_tenta_de_novo(monkeypatch):
    import asyncio
    from quantum import api
    from quantum.errors import CircuitError
    chamadas = {"n": 0}

    async def execute_worker(payload):
        chamadas["n"] += 1
        if chamadas["n"] < 3:
            raise CircuitError("OPTIMIZER_BUSY", "ocupado", status=429)
        return {"ok": True}

    monkeypatch.setattr(api, "execute_worker", execute_worker)
    monkeypatch.setattr(orquestrador, "_pausa_do_worker", original_sleep)
    assert asyncio.run(orquestrador._worker({"acao": "x"}, "teste")) == {"ok": True} and chamadas["n"] == 3

    async def invalido(payload):
        raise CircuitError("INVALID_CIRCUIT", "circuito inválido", status=422)

    monkeypatch.setattr(api, "execute_worker", invalido)
    with pytest.raises(CircuitError):  # erro do próprio código: não adianta repetir
        asyncio.run(orquestrador._worker({"acao": "x"}, "teste"))
