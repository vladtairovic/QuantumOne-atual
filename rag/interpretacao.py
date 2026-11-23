"""Interpretação de entradas: tipo, linguagem, biblioteca, intenção e complexidade.

Heurísticas determinísticas e explicáveis (sem LLM). Quando a entrada é
ambígua, o resultado traz hipóteses explícitas em vez de escolher uma
interpretação arbitrária. Usado pelo RAG (preferência de biblioteca) e pelo
motor de diagnóstico (escolha do analisador).
"""
import re

from .texto import sem_acentos

_QASM2 = re.compile(r"^\s*OPENQASM\s+2(\.0)?\s*;", re.MULTILINE)
_QASM3 = re.compile(r"^\s*OPENQASM\s+3(\.\d+)?\s*;", re.MULTILINE)
_QASM_CORPO = re.compile(r"^\s*(qreg|creg|qubit\s*\[|bit\s*\[|include\s+\"|gate\s+\w+|measure\s+\w|"
                         r"(h|x|y|z|cx|ccx|rz|rx|ry|u3?|t|s|swap|cz)\s+[\w\[\],\s]+;)", re.MULTILINE)
_PY = re.compile(r"^\s*(from\s+[\w.]+\s+import\b|import\s+\w|def\s+\w+\(|class\s+\w+|\w+\s*=\s*\w+\(|"
                 r"\w+\.(h|x|cx|measure|measure_all|append|compose|draw)\()", re.MULTILINE)
_TRACEBACK = re.compile(r"Traceback \(most recent call last\):|^\s*File \".+\", line \d+|"
                        r"^\w*(Error|Exception)(: .*)?$", re.MULTILINE)

BIBLIOTECAS = {
    "qiskit": r"\bqiskit\b|QuantumCircuit|transpile\(|qiskit_aer|AerSimulator|SamplerV2|EstimatorV2|PassManager",
    "qiskit_aer": r"qiskit_aer|AerSimulator|\baer\b",
    "qiskit_runtime": r"qiskit_ibm_runtime|QiskitRuntimeService|\bruntime\b",
    "pennylane": r"\bpennylane\b|\bqml\.|qml\.qnode",
    "cirq": r"\bcirq\b",
    "qsharp": r"\bQ#|\bqsharp\b|operation\s+\w+\s*\(",
    "braket": r"\bbraket\b|amazon braket",
    "qutip": r"\bqutip\b",
    "openqasm": r"OPENQASM|\bqasm\b|openqasm",
}

INTENCOES = [
    ("depuracao", r"\berro\b|\berror\b|exce|traceback|nao funciona|não funciona|falha|bug|corrig|conserta|"
                  r"invalido|inválido|quebrad|nao roda|não roda|por que .* (falha|da erro)"),
    ("otimizacao", r"otimiz|optimi|reduz|profundidade|depth|menos portas|cx count|swap|transpil"),
    ("conversao", r"convert|traduz|qasm ?3|qasm ?2|migrar|migra|portar|equivalente em"),
    ("implementacao", r"como (faco|faço|criar|implemento|construo|monto|escrevo)|exemplo de|implemente|"
                      r"escreva|crie um|construa|how (do|to)"),
    ("comparacao", r"diferenca|diferença|compar|versus|\bvs\b|melhor entre"),
    ("simulacao", r"simul|statevector|shots|contagens|counts|aer"),
    ("conceito", r"o que (e|é)|explique|significa|conceito|defina|por que|qual a ideia|teoria"),
]

TERMOS_AVANCADOS = r"vqe|qaoa|hamiltonian|hamiltoniano|tomograph|tomografia|qec|surface code|stabilizer|" \
                   r"estabilizador|zx|mps|tensor|variacional|variational|mitiga|ruido|ruído|noise|pulse"


def detectar_tipo(texto: str) -> dict:
    """Classifica a entrada: QASM2, QASM3, Python, traceback, linguagem natural ou mista."""
    sinais = {
        "qasm3": bool(_QASM3.search(texto)),
        "qasm2": bool(_QASM2.search(texto)),
        "qasm_sem_cabecalho": bool(_QASM_CORPO.search(texto)) and not (_QASM2.search(texto) or _QASM3.search(texto)),
        "python": bool(_PY.search(texto)),
        "traceback": bool(_TRACEBACK.search(texto)),
    }
    linhas = [l for l in texto.splitlines() if l.strip()]
    codigo = sum(1 for l in linhas if re.search(r"[;(){}\[\]=]|^\s*(from|import|def|qreg|creg)\b", l))
    proporcao_codigo = codigo / len(linhas) if linhas else 0.0
    partes = [k for k in ("qasm3", "qasm2", "python", "traceback") if sinais[k]]
    if sinais["qasm_sem_cabecalho"] and not sinais["python"]:
        partes.append("qasm_sem_cabecalho")
    hipoteses = []
    if not partes:
        tipo = "linguagem_natural"
    elif len(partes) == 1 and proporcao_codigo > 0.6:
        tipo = partes[0]
    else:
        tipo = "mista" if proporcao_codigo <= 0.6 or len(partes) > 1 else partes[0]
    if sinais["qasm_sem_cabecalho"] and not sinais["python"]:
        hipoteses.append("Parece OpenQASM sem a linha 'OPENQASM 2.0;' ou 'OPENQASM 3.0;'. "
                         "A versão foi inferida pelas declarações (qreg/creg indicam 2.0; qubit[n] indica 3.0).")
    if sinais["qasm2"] and sinais["qasm3"]:
        hipoteses.append("O texto declara OpenQASM 2 e 3 ao mesmo tempo; cada bloco será tratado separadamente.")
    versao_qasm = "3" if sinais["qasm3"] or re.search(r"^\s*(qubit|bit)\s*\[", texto, re.MULTILINE) else (
        "2" if sinais["qasm2"] or re.search(r"^\s*(qreg|creg)\b", texto, re.MULTILINE) else None)
    return {"tipo": tipo, "componentes": partes or ["linguagem_natural"],
            "versao_openqasm": versao_qasm, "proporcao_codigo": round(proporcao_codigo, 2),
            "hipoteses": hipoteses}


def detectar_bibliotecas(texto: str) -> list[str]:
    encontradas = [nome for nome, padrao in BIBLIOTECAS.items() if re.search(padrao, texto, re.IGNORECASE)]
    if "qiskit_aer" in encontradas or "qiskit_runtime" in encontradas:
        encontradas = [b for b in encontradas if b != "qiskit"] + ["qiskit"]
    return list(dict.fromkeys(encontradas))


def detectar_intencao(texto: str) -> tuple[str, list[str]]:
    normal = sem_acentos(texto.lower())
    candidatas = [nome for nome, padrao in INTENCOES if re.search(sem_acentos(padrao), normal)]
    if not candidatas:
        return "conceito", []
    return candidatas[0], candidatas[1:]


def interpretar(texto: str) -> dict:
    tipo = detectar_tipo(texto)
    intencao, secundarias = detectar_intencao(texto)
    if tipo["tipo"] in ("qasm2", "qasm3", "python", "qasm_sem_cabecalho") and intencao == "conceito":
        # Código sem pergunta: o pedido provável é revisar/diagnosticar.
        intencao, secundarias = "depuracao", ["otimizacao"]
    if tipo["tipo"] == "traceback" or "traceback" in tipo["componentes"]:
        intencao = "depuracao"
    bibliotecas = detectar_bibliotecas(texto)
    palavras = len(texto.split())
    avancado = bool(re.search(TERMOS_AVANCADOS, texto, re.IGNORECASE))
    complexidade = "avancada" if avancado or palavras > 400 else ("intermediaria" if palavras > 60 or
                                                                    tipo["tipo"] != "linguagem_natural" else "basica")
    hipoteses = list(tipo["hipoteses"])
    if secundarias:
        hipoteses.append(f"Intenção principal inferida: {intencao}; também pode ser: {', '.join(secundarias)}.")
    if tipo["tipo"] == "linguagem_natural" and palavras < 4:
        hipoteses.append("Pergunta muito curta: a resposta assume o sentido mais comum dos termos em computação quântica.")
    return {"tipo_entrada": tipo["tipo"], "componentes": tipo["componentes"],
            "versao_openqasm": tipo["versao_openqasm"], "bibliotecas": bibliotecas,
            "biblioteca_principal": bibliotecas[0] if bibliotecas else None,
            "intencao": intencao, "intencoes_secundarias": secundarias,
            "complexidade": complexidade, "hipoteses": hipoteses}
