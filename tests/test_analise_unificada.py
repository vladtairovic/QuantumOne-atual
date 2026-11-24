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
                  max_tokens=None, ao_parcial=None):
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
    client, _ = contexto
    d = client.post("/analise", json={"mensagem": "Otimize este circuito", "codigo": QASM_CORRIGIDO}).json()
    assert d["answer_mode"] == "local" and "Resultados do Qiskit" in d["resposta"]
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
