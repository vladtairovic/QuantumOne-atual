"""CLI interativa (cli.py, qone_core.py, qone_store.py) contra uma API simulada com o contrato atual."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from qone_core import Workspace, desfazer, editar_linhas, gravar, ultima_edicao
from qone_store import Registro

CLI = Path(__file__).resolve().parents[1] / "cli.py"
GIT = shutil.which("git")
QASM_QUEBRADO = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncnot q[0],q[1];\n'
QASM_CORRIGIDO = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n'


class FluxoCLI(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.repo = Path(t.name, "projeto")
        self.repo.mkdir()
        self.env = dict(os.environ, QONE_STATE_DIR=str(Path(t.name, "estado")), PYTHONIOENCODING="utf-8")
        self.src = self.repo / "circuito.py"
        self.src.write_bytes(b"def aplicar(x):\r\n    return x+1\r\n")
        (self.repo / "test_circuito.py").write_text(
            "import unittest\nfrom circuito import aplicar\n"
            "class Caso(unittest.TestCase):\n    def test_resultado(self):\n"
            "        self.assertEqual(aplicar(1), 1)\n")
        calls = self.calls = []

        class API(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def responder(self, resultado, status=200):
                corpo = json.dumps(resultado).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_GET(self):
                if self.path.startswith("/analise/progresso/"):
                    self.responder({"progress_id": self.path.rsplit("/", 1)[1], "concluido": False,
                                    "etapa": "Redigindo a resposta: consultando nvidia/modelo-teste (1/7)",
                                    "modelo": "nvidia/modelo-teste", "segundos_na_etapa": 0.5,
                                    "segundos_total": 0.9, "historico": []})
                elif self.path == "/health":
                    self.responder({"status": "ok"})
                else:
                    self.responder({"code": "NOT_FOUND", "message": "Rota não encontrada."}, 404)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, data))
                if self.path == "/assistente/proposta":
                    resultado = {"request_id": "00000000-0000-0000-0000-000000000000", "sources": [], "response": json.dumps({
                        "plano": "Corrigir a operação.", "riscos": "Muda o retorno.",
                        "verificacao": "Rodar Caso.test_resultado.",
                        "arquivos": {"circuito.py": [{"inicio": 2, "fim": 2, "linhas": ["    return x"]}]}
                    })}
                elif self.path == "/qiskit/optimize":
                    resultado = {"original": {"total_operacoes": 4, "profundidade": 3},
                                 "proposta": {"total_operacoes": 2, "profundidade": 2}, "perfil": "avancado",
                                 "status_verificacao": "nao_verificada", "motivo": "orçamento"}
                elif self.path == "/qiskit/diagnose":
                    quebrado = "cnot" in data["code"]
                    resultado = {"linguagem": "qasm", "resumo": {"erros": int(quebrado), "avisos": 0},
                                 "problemas": [{"codigo": "UNKNOWN_GATE", "severidade": "erro", "linha": 5,
                                                "mensagem": "Porta 'cnot' não existe em OpenQASM 2.",
                                                "correcao": {"descricao": "Trocar por cx."}}] if quebrado else [],
                                 "codigo_corrigido": QASM_CORRIGIDO if quebrado else None,
                                 "validacao": {"valido": True}}
                elif self.path == "/analise":
                    time.sleep(0.9)  # dá tempo de a CLI consultar o progresso ao vivo
                    resultado = {"resposta": "Resposta de teste", "conversation_id": "conversa-teste",
                                 "answer_mode": "llm", "status": "ok",
                                 "llm": {"provedor": "nvidia", "modelo": "nvidia/modelo-teste", "tentativas": []},
                                 "sources": [{"chunk_id": "c1", "source": "qone://base", "content": "trecho local",
                                              "origin": "local", "title": "Base"},
                                             {"chunk_id": "w1", "source": "https://exemplo.invalid", "content": "trecho web",
                                              "origin": "web", "title": "Web"}],
                                 "circuitos": [], "warnings": []}
                else:
                    resultado = {"code": "NOT_FOUND", "message": "Rota não encontrada."}
                    return self.responder(resultado, 404)
                self.responder(resultado)

        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{server.server_port}"

    def cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(CLI), "--api", self.api, "--repo", str(self.repo), *args],
                              cwd=self.repo, input=stdin, capture_output=True, encoding="utf-8", env=self.env, timeout=40)

    @unittest.skipUnless(GIT, "git não está instalado neste ambiente")
    def test_git_projeto_plano_aprovacao_teste_e_reversao(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=Teste", "-c", "user.email=teste@example.invalid",
                        "commit", "-qm", "Fixture"], cwd=self.repo, check=True)
        clone = self.repo.with_name("clone")
        subprocess.run(["git", "clone", "--quiet", "--local", str(self.repo), str(clone)], check=True)
        self.repo = clone
        self.src = clone / "circuito.py"
        antes = self.src.read_bytes()
        comandos = ["/git", "/testar unittest test_circuito.py", "sim", "analise este repositório e corrija os testes",
                    "/aplicar", "/testar unittest test_circuito.py", "sim", "/sair"]
        r = self.cli("--chat", stdin="\n".join(comandos) + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        for valor in ("Contexto selecionado:", "Plano:", "-    return x+1", "+    return x", "saída 1", "saída 0"):
            self.assertIn(valor, r.stdout)
        self.assertEqual(self.src.read_bytes(), antes.replace(b"x+1", b"x"))
        rota, corpo = self.calls[0]
        self.assertEqual(rota, "/assistente/proposta")
        self.assertIn("test_circuito.py", corpo["arquivos"])
        self.assertIn("AssertionError", corpo["resultado_testes"])
        self.assertTrue(corpo["progress_id"])
        r = self.cli("--chat", stdin="/desfazer\nsim\n/sair\n")
        self.assertIn("restaurada", r.stdout)
        self.assertEqual(self.src.read_bytes(), antes)

    def test_conversa_pela_analise_com_progresso_fontes_e_qiskit(self):
        r = self.cli("circuito.py", "liste")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("QuantumOne > Resposta de teste", r.stdout)
        self.assertIn("[Redigida por nvidia/modelo-teste]", r.stdout)
        self.assertIn("1 da base local, 1 da internet", r.stdout)
        self.assertIn("consultando nvidia/modelo-teste (1/7)", r.stderr)  # texto ao vivo
        rota, corpo = self.calls[0]
        self.assertEqual(rota, "/analise")
        self.assertEqual(corpo["mensagem"], "liste")
        self.assertEqual([a["nome"] for a in corpo["arquivos"]], ["circuito.py"])
        h = self.cli("--chat", stdin="/history\n/sair\n").stdout
        sid = re.search(r"[a-f0-9-]{36}", h)[0]
        r = self.cli("--chat", stdin=f"/retomar {sid}\ne agora?\n/fontes\n/qiskit\n/sair\n")
        self.assertIn("retomada", r.stdout)
        _, corpo = self.calls[1]
        self.assertEqual(corpo["mensagem"], "e agora?")
        self.assertEqual(corpo["arquivos"], [])  # arquivo não mudou: não é reenviado
        self.assertEqual(corpo["conversation_id"], "conversa-teste")
        self.assertIn("[Fonte 1] Base", r.stdout)
        self.assertIn("[Web 1] Web", r.stdout)
        self.assertEqual(self.calls[-2][0], "/qiskit/diagnose")  # Python é conferido antes de otimizar
        self.assertEqual(self.calls[-1][1]["format"], "python")
        self.assertEqual(self.calls[-1][1]["perfil"], "avancado")
        self.assertIs(self.calls[-1][1]["extrair_construcao"], True)
        self.assertIn("nao_verificada", r.stdout)
        self.assertIn("orçamento", r.stdout)

    def test_negacao_e_sem_efeitos(self):
        r = self.cli("--chat", stdin="/testar unittest test_circuito.py\nn\n/abrir ../fora.py\n/sair\n")
        self.assertIn("cancelada", r.stderr)
        self.assertIn("fora do escopo", r.stderr)
        self.assertNotIn("AssertionError", r.stdout)
        self.assertEqual(self.calls, [])
        roteiro = self.repo / "roteiro.txt"
        roteiro.write_text("/criar novo.py\n/aplicar\n")
        r = self.cli("--roteiro", str(roteiro))
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.repo / "novo.py").exists())
        r = self.cli("--roteiro", str(roteiro), "--permitir", "editar")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / "novo.py").exists())

    def test_edicao_multipla_conflito_e_rollback(self):
        with patch.dict(os.environ, self.env):
            ws = Workspace(self.repo)
            db = Registro(ws.raiz, self.api)
            a = self.src.read_bytes()
            b = (self.repo / "test_circuito.py").read_bytes()
            mudancas = {"circuito.py": (a, b"pass\n"), "test_circuito.py": (b, b"# novo\n")}
            from qone_core import substituir

            def falhar(ws, nome, antes, depois, modo):
                if nome == "test_circuito.py":
                    raise OSError("falha de teste")
                substituir(ws, nome, antes, depois, modo)

            with patch("qone_core.substituir", falhar), self.assertRaises(OSError):
                gravar(ws, db, "teste", mudancas)
            self.assertEqual(self.src.read_bytes(), a)
            gravar(ws, db, "teste", mudancas)
            eid = ultima_edicao(ws, db)["id"]
            self.src.write_text("# edição externa")
            with self.assertRaisesRegex(ValueError, "mudou"):
                desfazer(ws, db, eid)
            self.assertEqual(self.src.read_text(), "# edição externa")
            self.assertEqual((self.repo / "test_circuito.py").read_bytes(), b"# novo\n")

    def test_cancelamento_proposta_e_caminho(self):
        r = self.cli("--chat", stdin="/abrir circuito.py\n/propor corrigir\n/cancelar\n/aplicar\n/sair\n")
        self.assertIn("Plano: Corrigir a operação.", r.stdout)
        self.assertIn("Nenhuma proposta", r.stdout)
        self.assertIn(b"x+1", self.src.read_bytes())
        self.assertEqual(editar_linhas("vazio.py", b"", [{"inicio": 1, "fim": 0, "linhas": ["pass"]}]), b"pass\n")

    def test_qasm_otimizacao_no_perfil_maximo_e_bloqueio_de_credencial(self):
        (self.repo / "a.qasm").write_text("OPENQASM 2.0;")
        r = self.cli("--chat", stdin="/abrir a.qasm\n/qiskit 1\n/sair\n")
        self.assertEqual(self.calls, [("/qiskit/optimize", {"qasm": "OPENQASM 2.0;", "optimization_level": 1,
                                                            "perfil": "avancado"})])
        self.assertIn("total_operacoes", r.stdout)
        self.src.write_text('API_KEY = "valor_ficticio_de_teste"')
        r = self.cli("circuito.py", "liste")
        self.assertEqual(r.returncode, 1)
        self.assertIn("credencial", r.stderr)
        r = self.cli("circuito.py", "/qiskit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("credencial", r.stderr)
        self.src.write_text('NVIDIA = "nvapi-' + "x" * 30 + '"')
        r = self.cli("circuito.py", "liste")
        self.assertIn("credencial", r.stderr)
        self.assertEqual(len(self.calls), 1)

    def test_qiskit_em_python_com_erros_pede_diagnostico_antes(self):
        (self.repo / "legado.py").write_text("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\nqc.cnot(0, 1)\n")
        r = self.cli("--chat", stdin="/abrir legado.py\n/qiskit\n/sair\n")
        self.assertIn("erro(s) que impedem a otimização", r.stdout)
        self.assertIn("/diagnosticar", r.stdout)
        self.assertEqual([c[0] for c in self.calls], ["/qiskit/diagnose"])  # não otimizou

    def test_diagnosticar_vira_proposta_validada_pelo_qiskit(self):
        (self.repo / "bell.qasm").write_text(QASM_QUEBRADO)
        r = self.cli("--chat", stdin="/abrir bell.qasm\n/diagnosticar\n/aplicar\n/sair\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[UNKNOWN_GATE]", r.stdout)
        self.assertIn("+cx q[0],q[1];", r.stdout)
        self.assertIn("Alterações salvas: bell.qasm", r.stdout)
        self.assertEqual((self.repo / "bell.qasm").read_text(), QASM_CORRIGIDO)
        self.assertEqual(self.calls[0][0], "/qiskit/diagnose")

    def test_proposta_de_circuito_e_conferida_pelo_qiskit(self):
        import qone_core
        (self.repo / "bell.qasm").write_bytes(QASM_QUEBRADO.encode())  # LF: a CLI preserva as quebras do arquivo
        ws = Workspace(self.repo)
        retorno = json.dumps({"plano": "trocar cnot", "riscos": "nenhum", "verificacao": "Qiskit",
                              "arquivos": {"bell.qasm": [{"inicio": 5, "fim": 5, "linhas": ["cx q[0],q[1];"]}]}})
        dados = {}
        plano, alteracoes, _ = qone_core.proposta(ws, ["bell.qasm"], "corrigir", lambda d: dados.update(d) or {"response": retorno})
        self.assertEqual(alteracoes["bell.qasm"][1].decode(), QASM_CORRIGIDO)
        self.assertEqual(set(dados), {"pedido", "arquivos", "resultado_testes"})
        self.assertTrue(dados["arquivos"]["bell.qasm"].startswith("1: OPENQASM 2.0;"))
        self.assertTrue(qone_core.e_circuito("bell.qasm", b"") and not qone_core.e_circuito("util.py", b"x = 1"))

    def test_proposta_malformada_e_sintaxe(self):
        import qone_core
        ws = Workspace(self.repo)
        antes = self.src.read_bytes()
        for retorno in ["JSON incompleto", json.dumps({
            "plano": "editar", "riscos": "teste", "verificacao": "testar",
            "arquivos": {"circuito.py": [{"inicio": 1, "fim": 2, "linhas": ["def ("]}]}
        })]:
            with self.assertRaises((SyntaxError, ValueError)):
                qone_core.proposta(ws, ["circuito.py"], "corrigir", lambda _: {"response": retorno})
            self.assertEqual(antes, self.src.read_bytes())
        # JSON cercado por texto ou bloco ```json também é aceito
        cercado = "Aqui está:\n" + json.dumps({"plano": "p", "riscos": "r", "verificacao": "v", "arquivos": {}})
        self.assertEqual(qone_core.proposta(ws, ["circuito.py"], "x", lambda _: {"response": cercado})[1], {})

    def test_insercao_que_repete_as_linhas_seguintes_nao_duplica(self):
        original = b"from qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\n"
        repetida = [{"inicio": 1, "fim": 0, "linhas": ['"""Doc."""', "", "from qiskit import QuantumCircuit"]}]
        self.assertEqual(editar_linhas("a.py", original, repetida),
                         b'"""Doc."""\n\nfrom qiskit import QuantumCircuit\nqc = QuantumCircuit(1)\n')
        # inserção legítima (nada repetido) continua igual
        normal = [{"inicio": 2, "fim": 1, "linhas": ["import math"]}]
        self.assertEqual(editar_linhas("a.py", original, normal),
                         b"from qiskit import QuantumCircuit\nimport math\nqc = QuantumCircuit(1)\n")

    def test_aplicar_e_reverter_dois_arquivos(self):
        ws = Workspace(self.repo)
        with patch.dict(os.environ, self.env):
            db = Registro(ws.raiz, self.api)
            orig = self.src.read_bytes()
            alteracoes = {"circuito.py": (orig, b"pass\n"), "exemplos/novo.py": (None, b"pass\n")}
            gravar(ws, db, "teste", alteracoes)
            self.assertEqual((self.repo / "exemplos/novo.py").read_bytes(), b"pass\n")
            desfazer(ws, db, ultima_edicao(ws, db)["id"])
            self.assertFalse((self.repo / "exemplos/novo.py").exists())
            self.assertEqual(self.src.read_bytes(), orig)

    def test_api_fora_do_ar_explica_como_iniciar(self):
        r = subprocess.run([sys.executable, str(CLI), "--api", "http://127.0.0.1:9", "--repo", str(self.repo),
                            "circuito.py", "liste"], cwd=self.repo, capture_output=True, encoding="utf-8",
                           env=self.env, timeout=40)
        self.assertEqual(r.returncode, 1)
        self.assertIn("docker compose up -d --wait api", r.stderr)

    def test_timeout(self):
        alvo = self.repo / "test_lento.py"
        alvo.write_text("import time, unittest\nclass Lento(unittest.TestCase):\n def test_demora(self): time.sleep(30)\n")
        r = self.cli("--chat", "--timeout-testes", "1", stdin="/testar unittest test_lento.py\ns\n/sair\n")
        self.assertIn("tempo excedido", r.stdout)


if __name__ == "__main__":
    unittest.main()
