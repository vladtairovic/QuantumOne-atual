"""Motor universal de interpretação, diagnóstico e correção do QuantumOne.

Pipeline: detectar tipo -> identificar linguagem/versão/biblioteca -> inferir
intenção -> sintaxe -> semântica -> causa raiz -> correção -> validação da
versão corrigida -> explicação. Nunca executa o código recebido: a validação
usa os mesmos carregadores restritos do otimizador (parser OpenQASM do Qiskit
e o interpretador de construção Python), dentro do worker isolado.
"""
import difflib
import re
from time import perf_counter

from rag.interpretacao import interpretar
from . import diagnostico_python, diagnostico_qasm
from .errors import CircuitError
from .migracao import REGRAS

ORDEM_CONFIANCA = {"alta": 2, "media": 1, "baixa": 0}

PADROES_TRACEBACK = [
    (r"No module named '(qiskit_aer|qiskit\.providers\.aer)'", "MISSING_PACKAGE",
     "O simulador Aer é um pacote separado do Qiskit.", "Instale com 'pip install qiskit-aer' e importe de qiskit_aer."),
    (r"cannot import name '(\w+)' from 'qiskit'", "REMOVED_API", None, None),
    (r"No module named '(qiskit\.(?:pulse|opflow|algorithms|extensions|tools|test)[\w.]*)'", "REMOVED_API", None, None),
    (r"'QuantumCircuit' object has no attribute '(\w+)'", "REMOVED_METHOD", None, None),
    (r"Index (\d+) out of range for size (\d+)", "INDEX_OUT_OF_RANGE",
     "Um índice de qubit ou bit é maior ou igual ao tamanho do circuito.",
     "Aumente o circuito ou corrija o índice, conforme o circuito pretendido."),
    (r"duplicate qubit arguments", "DUPLICATE_QUBIT", "Uma porta de vários qubits recebeu o mesmo qubit duas vezes.",
     "Use qubits distintos para controle e alvo."),
    (r"No counts for experiment|does not have any classical bits|no measurements", "MISSING_MEASUREMENTS",
     "O circuito foi executado sem medições, então não há contagens.",
     "Adicione qc.measure_all() ou meça os qubits em bits clássicos; para o estado exato use Statevector."),
    (r"(?i)unbound parameter|parameters? .*not.*(bound|assigned)", "UNBOUND_PARAMETERS",
     "O circuito tem parâmetros sem valor.", "Atribua valores com assign_parameters ou passe-os na PUB."),
    (r"Unable to translate the operations", "TRANSPILER_BASIS",
     "O transpilador não encontrou como traduzir uma porta para o conjunto de portas pedido.",
     "Confira basis_gates/target ou decomponha a porta personalizada antes de transpilar."),
    (r"QASM2ParseError|QASM2Error", "QASM_PARSE", "O parser OpenQASM 2 rejeitou o programa.",
     "Envie o programa OpenQASM para o diagnóstico completo de todas as linhas."),
    (r"NameError: name '(\w+)' is not defined", "UNDEFINED_NAME", None, None),
]


def _segmentos(texto, interpretacao):
    """Separa código, traceback e texto em entradas mistas."""
    blocos = re.findall(r"```(\w*)\n(.*?)```", texto, flags=re.DOTALL)
    codigo, linguagem = None, None
    if blocos:
        for lang, corpo in blocos:
            if lang.lower() in ("qasm", "openqasm") or re.search(r"\b(OPENQASM|qreg|qubit\[)", corpo):
                return corpo, "qasm"
            if lang.lower() in ("python", "py") or re.search(r"(import|QuantumCircuit|\.h\()", corpo):
                codigo, linguagem = corpo, "python"
        if codigo:
            return codigo, linguagem
    tipo = interpretacao["tipo_entrada"]
    if tipo in ("qasm2", "qasm3", "qasm_sem_cabecalho") or "qasm2" in interpretacao["componentes"] \
            or "qasm3" in interpretacao["componentes"]:
        inicio = re.search(r"^\s*(OPENQASM|include|qreg|creg|qubit\s*\[|bit\s*\[)", texto, re.MULTILINE)
        return (texto[inicio.start():] if inicio else texto), "qasm"
    if "python" in interpretacao["componentes"]:
        linhas, anterior_frame = [], False
        for l in texto.split("\n"):
            if re.match(r"\s*(Traceback \(most|File \"|\w*(Error|Exception)(:|$)|\^+\s*$|~+\s*$)", l):
                anterior_frame = bool(re.match(r"\s*File \"", l))
                continue
            if anterior_frame and l.startswith(" "):
                anterior_frame = False
                continue  # linha de código ecoada pelo traceback, não é o programa
            anterior_frame = False
            linhas.append(l)
        programa = "\n".join(linhas).strip("\n")
        if re.search(r"(import\s|QuantumCircuit|\w+\s*=|\.\w+\()", programa):
            return programa + "\n", "python"
    return None, None


def analisar_traceback(texto):
    problemas = []
    tipo = re.findall(r"^(?:[\w]+\.)*(\w+(?:Error|Exception)):\s*(.*)$", texto, re.MULTILINE)
    linha = re.findall(r'File "[^"]*", line (\d+)', texto)
    for padrao, codigo, causa, correcao in PADROES_TRACEBACK:
        m = re.search(padrao, texto)
        if not m:
            continue
        if codigo == "REMOVED_API":
            nome = m.group(1)
            regra = next((r for r in REGRAS if re.search(r["padrao"], f"from qiskit import {nome}") or nome in r["antigo"]), None)
            causa = f"'{nome}' foi removido do Qiskit." + (f" Removido na versão {regra['removido_em']}." if regra else "")
            correcao = f"Substituir por: {regra['novo']}." if regra else "Consulte o guia de migração para Qiskit 2.x."
        elif codigo == "REMOVED_METHOD":
            nome = m.group(1)
            renomeado = diagnostico_python.METODOS_RENOMEADOS.get(nome)
            causa = f"QuantumCircuit.{nome} não existe no Qiskit 2.x."
            correcao = f"Usar QuantumCircuit.{renomeado[0]}." if renomeado else "Confira o nome do método na documentação do QuantumCircuit."
        elif codigo == "UNDEFINED_NAME":
            nome = m.group(1)
            modulo = diagnostico_python.CONSTRUTORES.get(nome)
            causa = f"'{nome}' foi usado sem ser importado ou definido."
            correcao = f"Adicionar 'from {modulo} import {nome}'." if modulo else "Defina ou importe o nome antes do uso."
        problemas.append({"codigo": codigo, "severidade": "erro", "linha": int(linha[-1]) if linha else None,
                          "trecho": m.group(0)[:200], "mensagem": f"{tipo[-1][0]}: {tipo[-1][1][:200]}" if tipo else m.group(0),
                          "causa_raiz": causa, "correcao": {"tipo": "manual", "descricao": correcao},
                          "alternativas": [], "confianca": "alta" if codigo not in ("QASM_PARSE",) else "media",
                          "categoria": "diagnostico_de_execucao"})
        break
    if not problemas and tipo:
        problemas.append({"codigo": "UNCLASSIFIED_EXCEPTION", "severidade": "erro",
                          "linha": int(linha[-1]) if linha else None, "trecho": "", "mensagem": f"{tipo[-1][0]}: {tipo[-1][1][:200]}",
                          "causa_raiz": "Exceção sem padrão conhecido pelo QuantumOne.",
                          "correcao": {"tipo": "manual", "descricao": "Envie também o código que gerou o erro para análise."},
                          "alternativas": [], "confianca": "baixa", "categoria": "diagnostico_de_execucao"})
    return problemas


def _esvaziar(linhas, n):
    """Esvazia a linha n (1-based); se for cabeçalho de bloco (termina em ':'), esvazia o corpo também."""
    cabecalho = linhas[n - 1]
    removidas = [cabecalho.strip()]
    linhas[n - 1] = ""
    if cabecalho.split("#", 1)[0].rstrip().endswith(":"):
        recuo = len(cabecalho) - len(cabecalho.lstrip())
        k = n
        while k < len(linhas):
            atual = linhas[k]
            if atual.strip() and len(atual) - len(atual.lstrip()) <= recuo:
                break
            if atual.strip():
                removidas.append(atual.strip())
            linhas[k] = ""
            k += 1
    return removidas


def _reduzir_import(linha):
    """'from qiskit import QuantumCircuit, transpile' -> mantém só os nomes de construção.

    Devolve (nova_linha, nomes_removidos) ou None quando a linha não é um import misto
    (sem nada aproveitável, a linha inteira é esvaziada pelo chamador)."""
    from .python_parser import CONSTRUCTORS, MATH
    m = re.match(r"(\s*)from\s+(qiskit(?:\.circuit)?|math)\s+import\s+\(?([^()#]+?)\)?\s*(?:#.*)?$", linha)
    if not m:
        return None
    permitidos = MATH if m.group(2) == "math" else CONSTRUCTORS
    nomes = [x.strip() for x in m.group(3).split(",") if x.strip()]
    mantidos = [x for x in nomes if x.split(" as ")[0].strip() in permitidos]
    removidos = [x for x in nomes if x not in mantidos]
    if not mantidos or not removidos:
        return None
    return f"{m.group(1)}from {m.group(2)} import {', '.join(mantidos)}", removidos


def extrair_construcao(codigo):
    """Mantém só as linhas que o interpretador restrito aceita (construção do circuito).

    Linhas de execução, simulação, impressão ou bibliotecas externas são esvaziadas
    (a numeração é preservada) e devolvidas em ``ignoradas``. Blocos não suportados
    (por exemplo ``if __name__ == "__main__":`` ou ``def``) são esvaziados inteiros.
    Nada é executado.
    """
    from .python_parser import carregar_circuito_python
    linhas = codigo.split("\n")
    ignoradas = []
    for _ in range(80):
        try:
            circuito = carregar_circuito_python("\n".join(linhas))
            return "\n".join(linhas), ignoradas, circuito
        except CircuitError as erro:
            n = erro.details.get("line")
            if erro.code != "UNSUPPORTED_PYTHON" or not n or n > len(linhas) or not linhas[n - 1].strip():
                raise
            reduzida = _reduzir_import(linhas[n - 1])
            if reduzida:
                linhas[n - 1], removidos = reduzida
                ignoradas.append({"linha": n, "texto": f"import de {', '.join(removidos)}"[:160], "motivo": erro.message})
                continue
            for texto in _esvaziar(linhas, n):
                ignoradas.append({"linha": n, "texto": texto[:160], "motivo": erro.message})
    raise CircuitError("INVALID_CIRCUIT", "Muitas instruções fora do subconjunto de construção.")


def _validar(codigo, linguagem):
    """Carrega o código corrigido com os carregadores restritos do otimizador."""
    from .qiskit_service import carregar_circuito_qasm, obter_metricas, validar_limites_circuito
    ignoradas = []
    if linguagem == "qasm":
        circuito = carregar_circuito_qasm(codigo)
    else:
        _, ignoradas, circuito = extrair_construcao(codigo)
        validar_limites_circuito(circuito)
    return circuito, obter_metricas(circuito), ignoradas


def diagnosticar(texto: str, formato: str = "auto", politica: str = "conservadora") -> dict:
    inicio = perf_counter()
    if politica not in ("conservadora", "expandir_registradores"):
        raise CircuitError("INVALID_REQUEST", "Política de correção desconhecida.")
    interpretacao = interpretar(texto)
    problemas = analisar_traceback(texto) if "traceback" in interpretacao["componentes"] else []
    if formato in ("qasm", "python"):
        codigo, linguagem = texto, formato
    else:
        codigo, linguagem = _segmentos(texto, interpretacao)
    resultado = {"interpretacao": interpretacao, "linguagem": linguagem, "politica": politica,
                 "codigo_original": codigo, "codigo_corrigido": None, "diff": "", "problemas": problemas,
                 "validacao": {"executada": False}, "limitacoes": [], "sugestoes": []}
    if codigo is None:
        if not problemas:
            resultado["limitacoes"].append("Nenhum código OpenQASM ou Python foi identificado; use a consulta "
                                           "à base de conhecimento para perguntas conceituais.")
        return _finalizar(resultado, inicio)
    analise = diagnostico_qasm.analisar(codigo, politica) if linguagem == "qasm" else \
        diagnostico_python.analisar(codigo, politica)
    resultado["problemas"].extend(analise["problemas"])
    corrigido = analise["codigo_corrigido"]
    resultado["codigo_corrigido"] = corrigido
    resultado["diff"] = "".join(difflib.unified_diff(
        codigo.splitlines(keepends=True), corrigido.splitlines(keepends=True), "original", "corrigido"))
    validacao = {"executada": True, "metodo": "parser OpenQASM 2 do Qiskit" if linguagem == "qasm"
                 else "interpretador restrito de construção (sem execução)"}
    try:
        circuito, metricas, ignoradas = _validar(corrigido, linguagem)
        validacao.update(valido=True, metricas=metricas, instrucoes_ignoradas=ignoradas)
        reanalise = (diagnostico_qasm if linguagem == "qasm" else diagnostico_python).analisar(corrigido, politica)
        restantes = [p for p in reanalise["problemas"] if p["severidade"] == "erro"]
        validacao["erros_restantes_na_reanalise"] = len(restantes)
        if ignoradas:
            resultado["limitacoes"].append(
                f"{len(ignoradas)} instrução(ões) fora do subconjunto de construção (simulação, impressão, "
                "visualização ou APIs externas) não foram executadas na validação.")
        if any(p["codigo"] == "UNBOUND_PARAMETERS" for p in resultado["problemas"]):
            resultado["limitacoes"].append("Parâmetros simbólicos não foram atribuídos; a validação usa só a estrutura.")
        resultado["sugestoes"].append("O circuito corrigido pode ser enviado ao otimizador (/qiskit/optimize) "
                                      "para análise estrutural, otimização e verificação de equivalência.")
    except CircuitError as erro:
        validacao.update(valido=False, erro={"code": erro.code, "message": erro.message, "details": erro.details})
        resultado["limitacoes"].append("A versão corrigida ainda não foi aceita pelo carregador; revise as "
                                       "alternativas dos problemas de baixa confiança.")
    resultado["validacao"] = validacao
    if any(p["correcao"].get("tipo") == "remocao" for p in resultado["problemas"]):
        resultado["limitacoes"].append(
            "Instruções com intenção indeterminável foram removidas na versão corrigida (política conservadora); "
            "ela é um circuito válido, não uma reconstrução garantida da intenção original." if politica == "conservadora"
            else "Registradores foram ampliados para caber os índices usados; confirme se esse era o tamanho pretendido.")
    resultado["limitacoes"].append("O código original com erros não forma um circuito, então não há equivalência "
                                   "formal entre original e corrigido; apenas correções inequívocas preservam a intenção.")
    return _finalizar(resultado, inicio)


def _finalizar(resultado, inicio):
    erros = [p for p in resultado["problemas"] if p["severidade"] == "erro"]
    niveis = [ORDEM_CONFIANCA[p["confianca"]] for p in erros] or [2]
    resultado["confianca_global"] = {2: "alta", 1: "media", 0: "baixa"}[min(niveis)]
    resultado["resumo"] = {
        "erros": len(erros), "avisos": len(resultado["problemas"]) - len(erros),
        "correcoes_automaticas": sum(1 for p in erros if p["confianca"] == "alta"),
        "decisoes_ambiguas": sum(1 for p in erros if p["confianca"] == "baixa"),
        "categorias": sorted({p["categoria"] for p in resultado["problemas"]}),
    }
    resultado["tempo_segundos"] = round(perf_counter() - inicio, 4)
    resultado["problemas"].sort(key=lambda p: (p["linha"] or 0))
    return resultado
