"""Benchmark reproduzível do motor de circuitos do QuantumOne.

Gera uma suíte progressiva (2 a 30 qubits) com circuitos aleatórios,
estruturados, redundantes, emaranhados, com medições e com restrição de
hardware, executa o motor em processo e grava métricas em JSON.

Uso:
    python scripts/benchmark_qone.py --saida docs/benchmarks/motor-atual.json
    python scripts/benchmark_qone.py --perfil balanceado --saida ...

O mesmo script roda contra a versão antiga (somente ``processar_circuito``
com nível 0-3) e contra a nova (perfis e verificação adaptativa), para
permitir comparar com a linha de base.
"""
import argparse
import inspect
import json
import platform
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qiskit import QuantumCircuit, qasm2  # noqa: E402
from qiskit.circuit.library import QFT  # noqa: E402
from qiskit.circuit.random import random_circuit  # noqa: E402

TAMANHOS = (2, 4, 5, 8, 12, 16, 20, 25, 30)


def ghz(n):
    qc = QuantumCircuit(n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    return qc


def redundante(n):
    """Oportunidades conhecidas: pares inversos, rotações fundíveis, CX duplicados."""
    qc = QuantumCircuit(n)
    for i in range(n):
        qc.h(i)
        qc.h(i)
        qc.rz(0.3, i)
        qc.rz(0.4, i)
        qc.t(i)
        qc.tdg(i)
    for i in range(n - 1):
        qc.cx(i, i + 1)
        qc.cx(i, i + 1)
        qc.cx(i, i + 1)
    return qc


def emaranhado(n, camadas=6):
    """Brickwork: camadas alternadas de rotações e CX, difícil de simplificar."""
    qc = QuantumCircuit(n)
    for c in range(camadas):
        for i in range(n):
            qc.ry(0.1 * (i + 1) + 0.37 * c, i)
            qc.rz(0.05 * (i + 2) + 0.21 * c, i)
        for i in range(c % 2, n - 1, 2):
            qc.cx(i, i + 1)
    return qc


def aleatorio(n):
    return random_circuit(n, depth=8, max_operands=2, seed=1000 + n)


def com_medicoes(n):
    qc = ghz(n)
    qc.measure_all()
    return qc


def qft(n):
    return QFT(n, do_swaps=True).decompose()


GERADORES = {
    "aleatorio": aleatorio,
    "estruturado_ghz": ghz,
    "estruturado_qft": qft,
    "redundante": redundante,
    "emaranhado": emaranhado,
    "com_medicoes": com_medicoes,
}


def suite():
    for n in TAMANHOS:
        for nome, gerar in GERADORES.items():
            if nome == "estruturado_qft" and n > 20:
                continue  # QFT exata de 25-30 qubits não é um caso realista sem aproximação.
            yield nome, n, qasm2.dumps(gerar(n))


def executar(perfil, nivel, hardware):
    from quantum import qiskit_service as servico
    parametros = inspect.signature(servico.processar_circuito).parameters
    novo = "perfil" in parametros
    linhas = []
    for nome, n, codigo in suite():
        kwargs = {}
        if novo:
            kwargs["perfil"] = perfil
            if hardware:
                kwargs["hardware"] = {"topologia": hardware}
        tracemalloc.start()
        inicio = time.perf_counter()
        try:
            r = servico.processar_circuito(codigo, "qasm", nivel, **kwargs)
            erro = None
        except Exception as exc:  # benchmark registra a falha, não interrompe a suíte
            r, erro = None, f"{type(exc).__name__}: {str(exc)[:200]}"
        duracao = time.perf_counter() - inicio
        _, pico = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        linha = {"caso": nome, "qubits": n, "tempo_s": round(duracao, 4),
                 "pico_memoria_python_mb": round(pico / 2**20, 2), "erro": erro}
        if r:
            o, p = r["original"], r["proposta"]
            linha.update(
                portas_original=o["total_operacoes"], portas_final=p["total_operacoes"],
                profundidade_original=o["profundidade"], profundidade_final=p["profundidade"],
                dois_qubits_original=o["operacoes_dois_qubits"], dois_qubits_final=p["operacoes_dois_qubits"],
                equivalente=r["equivalente"], status_verificacao=r["status_verificacao"],
                metodo_verificacao=(r.get("verificacao") or {}).get("metodo"),
                estrategia=r.get("estrategia_aplicada"),
            )
        linhas.append(linha)
        print(json.dumps(linha, ensure_ascii=False), flush=True)
    return novo, linhas


def resumo(linhas):
    ok = [l for l in linhas if not l["erro"]]
    verificados = [l for l in ok if l["equivalente"] is True]
    nao_verificados = [l for l in ok if l["equivalente"] is None]

    def reducao(chave_o, chave_f):
        base = sum(l[chave_o] for l in ok)
        final = sum(l[chave_f] for l in ok)
        return round(100 * (base - final) / base, 2) if base else 0.0

    return {
        "casos": len(linhas), "falhas": len(linhas) - len(ok),
        "equivalencia_verificada": len(verificados),
        "equivalencia_nao_verificada": len(nao_verificados),
        "equivalencia_refutada": sum(l["equivalente"] is False for l in ok),
        "taxa_verificacao": round(len(verificados) / len(ok), 4) if ok else 0.0,
        "reducao_total_portas_pct": reducao("portas_original", "portas_final"),
        "reducao_total_dois_qubits_pct": reducao("dois_qubits_original", "dois_qubits_final"),
        "reducao_total_profundidade_pct": reducao("profundidade_original", "profundidade_final"),
        "tempo_total_s": round(sum(l["tempo_s"] for l in linhas), 2),
        "maior_qubits_verificado": max((l["qubits"] for l in verificados), default=0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", type=Path, required=True)
    parser.add_argument("--perfil", default="balanceado")
    parser.add_argument("--nivel", type=int, default=3)
    parser.add_argument("--hardware", default=None, help="linear, anel, grade ou heavy_hex (somente motor novo)")
    args = parser.parse_args()
    novo, linhas = executar(args.perfil, args.nivel, args.hardware)
    import qiskit
    relatorio = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "motor": "perfis" if novo else "linha_de_base_transpile",
        "perfil": args.perfil if novo else None, "nivel": args.nivel, "hardware": args.hardware,
        "qiskit": qiskit.__version__, "python": platform.python_version(), "plataforma": platform.platform(),
        "resumo": resumo(linhas), "casos": linhas,
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(relatorio["resumo"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
