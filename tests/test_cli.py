"""Integração CLI -> Backend -> Motor quântico/RAG (mesma API do Desktop Chat)."""
import io

from qone_cli import executar
from tests.test_integracao import contexto  # noqa: F401  (fixture)

QASM_RUIM = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[5];\ncnot q[0],q[1];\n'


def rodar(argv, cliente):
    saida = io.StringIO()
    codigo = executar(argv, cliente=cliente, saida=saida)
    return codigo, saida.getvalue()


def test_cli_diagnostica_e_otimiza_pelo_backend(contexto, tmp_path):
    client, _ = contexto
    arquivo = tmp_path / "ruim.qasm"
    arquivo.write_text(QASM_RUIM, encoding="utf-8")
    codigo, texto = rodar(["diagnosticar", str(arquivo)], client)
    assert codigo == 0
    assert "INDEX_OUT_OF_RANGE" in texto and "UNKNOWN_GATE" in texto and "Código corrigido (validado)" in texto
    corrigido = tmp_path / "corrigido.qasm"
    corrigido.write_text('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\nh q[0];\nh q[0];\ncx q[0],q[1];\nccx q[0],q[1],q[2];\n',
                         encoding="utf-8")
    codigo, texto = rodar(["otimizar", str(corrigido), "--perfil", "avancado"], client)
    assert codigo == 0 and "Verificação: operador (exata) -> equivalente=True" in texto
    assert "CX equivalentes:" in texto and "OPENQASM 2.0;" in texto


def test_cli_pergunta_usa_resposta_local_sem_web(contexto):
    client, _ = contexto
    client.post("/documents", files={"file": ("medicao.txt", "Use QuantumCircuit.measure_all para medir todos os qubits do circuito.".encode())})
    codigo, texto = rodar(["perguntar", "Como medir todos os qubits?"], client)
    assert codigo == 0 and "[extrativa local (sem LLM)]" in texto and "Fontes consultadas (1)" in texto


def test_cli_reporta_erro_da_api(contexto, tmp_path):
    client, _ = contexto
    arquivo = tmp_path / "x.qasm"
    arquivo.write_text("OPENQASM 2.0;\nqreg q[1];\nx q[3];\n", encoding="utf-8")
    codigo, texto = rodar(["otimizar", str(arquivo)], client)
    assert codigo == 1 and "INVALID_CIRCUIT" in texto
