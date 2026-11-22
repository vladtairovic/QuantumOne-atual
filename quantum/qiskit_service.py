import re

from qiskit import QuantumCircuit, transpile, qasm2
from qiskit.quantum_info import Operator
from qiskit.transpiler import CouplingMap
from qiskit.exceptions import QiskitError
from qiskit.circuit import Gate

from hashlib import sha256
from time import perf_counter
from .config import get_limits, validate_source
from .errors import CircuitError, enforce_limit

LIM_MAX_EQUIV = 10  # Hard safety ceiling for the public matrix-comparison helper.


def _without_qasm_comments(source):
    # A slash inside an include string is a path character, not a comment.
    return re.sub(r'"[^"]*"|//[^\n]*|/\*.*?\*/',
                  lambda m: m.group() if m.group().startswith('"') else " " + "\n" * m.group().count("\n"),
                  source, flags=re.DOTALL)


def validar_circuito(circuito):
    if not isinstance(circuito, QuantumCircuit):
        raise TypeError("O circuito deve ser um QuantumCircuit.")


def _expanded_operations(circuit, maximum):
    """Count custom definitions and control-flow bodies without expanding the DAG."""
    memo = {}
    def count(current, level=0):
        enforce_limit("definition_depth", level, 80)
        total = 0
        for item in current.data:
            op = item.operation
            blocks = getattr(op, "blocks", ())
            if blocks:
                weight = 1 + sum(count(block, level + 1) for block in blocks)
            elif (type(op).__module__ or "").startswith("qiskit.qasm2") and op.definition is not None:
                definition = op.definition
                key = id(definition)
                if key not in memo:
                    # Keep a strong reference: SDK instruction wrappers may be
                    # short-lived, and recycled ids must not reuse another count.
                    memo[key] = (definition, count(definition, level + 1))
                weight = max(1, memo[key][1])
            else:
                weight = 1
            total += weight
            enforce_limit("expanded_operations", total, maximum)
        return total
    return count(circuit)


def validar_limites_circuito(circuito):
    limits = get_limits()
    enforce_limit("qubits", circuito.num_qubits, limits.max_qubits)
    enforce_limit("classical_bits", circuito.num_clbits, limits.max_classical_bits)
    enforce_limit("circuit_gates", len(circuito.data), limits.max_circuit_gates)
    _expanded_operations(circuito, limits.max_circuit_gates)
    enforce_limit("circuit_depth", circuito.depth(), limits.max_circuit_depth)


def expandir_definicoes_qasm(circuit):
    """Inline bounded user definitions before name-based SDK optimization passes."""
    expanded = circuit.copy_empty_like()
    for item in circuit.data:
        op = item.operation
        if type(op) is Gate and op.definition is None:
            raise CircuitError("UNSUPPORTED_OPERATION", "Porta opaca sem definição não pode ser otimizada.",
                               {"reason": "Forneça uma definição para a porta.", "operation": op.name[:100]})
        blocks = getattr(op, "blocks", ())
        if blocks:
            expanded.append(op.replace_blocks([expandir_definicoes_qasm(b) for b in blocks]), item.qubits, item.clbits)
        elif (type(op).__module__ or "").startswith("qiskit.qasm2") and op.definition is not None:
            expanded.compose(expandir_definicoes_qasm(op.definition), qubits=item.qubits, clbits=item.clbits, inplace=True)
        else:
            expanded.append(item)
    return expanded


def carregar_circuito_qasm(texto_qasm: str):
    if not isinstance(texto_qasm, str):
        raise TypeError("O OpenQASM deve ser uma string.")
    limits = validate_source(texto_qasm)
    # Preserve newlines so parser diagnostics refer to the original input.
    clean = _without_qasm_comments(texto_qasm)
    for match in re.finditer(r'\binclude\s+"([^"]*)"', clean):
        if match.group(1) != "qelib1.inc":
            raise CircuitError("INVALID_CIRCUIT", "Somente include qelib1.inc é permitido.",
                               {"line": clean[:match.start()].count("\n") + 1, "reason": "Include externo não permitido."})
    declarations = re.findall(r"\b(qreg|creg)\s+[A-Za-z_][A-Za-z0-9_]*\s*\[\s*(\d+)\s*\]\s*;", clean)
    if any(len(n) > 20 for _, n in declarations):
        raise CircuitError("INVALID_CIRCUIT", "Tamanho de registrador não representável.",
                           {"reason": "Declaração de registrador fora da faixa numérica do OpenQASM."})
    enforce_limit("qubits", sum(int(n) for kind, n in declarations if kind == "qreg"), limits.max_qubits)
    enforce_limit("classical_bits", sum(int(n) for kind, n in declarations if kind == "creg"), limits.max_classical_bits)
    try:
        declared = set(re.findall(r"\b(?:gate|opaque)\s+([A-Za-z_][A-Za-z0-9_]*)", clean))
        legacy = [gate for gate in qasm2.LEGACY_CUSTOM_INSTRUCTIONS if gate.name not in declared]
        circuito = qasm2.loads(texto_qasm, include_path=(), custom_instructions=legacy)
    except (qasm2.QASM2Error, RecursionError) as exc:
        reason = str(exc)[:1000]
        line = re.search(r":(\d+),(\d+):", reason)
        invalid_index = re.search(r"index (\d+) is out-of-range for register '([^']+)' of size (\d+)", reason)
        if invalid_index:
            index, register, size = invalid_index.groups()
            reason = (f"{register}[{index}] não existe: o registrador {register} tem {size} bits, "
                      f"com índices de 0 a {int(size) - 1}. Corrija o índice ou a declaração "
                      "conforme o circuito pretendido. O analisador parou no primeiro erro.")
        raise CircuitError("INVALID_CIRCUIT", "OpenQASM 2 inválido.",
                           {"line": int(line[1]) if line else 1, "reason": reason}) from exc
    validar_limites_circuito(circuito)
    circuito = expandir_definicoes_qasm(circuito)
    validar_limites_circuito(circuito)
    return circuito

def exportar_circuito_qasm(circuito: QuantumCircuit):
    """
    Converte um QuantumCircuit em texto OpenQASM 2.
    """
    validar_circuito(circuito)

    try:
        return qasm2.dumps(circuito)

    except qasm2.QASM2Error as erro:
        raise ValueError(
            "Não foi possível exportar o circuito para OpenQASM 2."
        ) from erro


def processar_circuito(code: str, formato: str = "auto", nivel_otimizacao: int = 3):
    started = perf_counter()
    validate_source(code)
    if formato == "auto":
        clean = _without_qasm_comments(code).lstrip()
        formato = "qasm" if clean.startswith("OPENQASM") else "python"
    if formato == "qasm":
        original = carregar_circuito_qasm(code)
    elif formato == "python":
        from .python_parser import carregar_circuito_python
        original = carregar_circuito_python(code)
        validar_limites_circuito(original)
    else:
        raise CircuitError("INVALID_REQUEST", "Formato não suportado.")
    passes = set()
    otimizado = materializar_layout(original, otimizar_circuito(original, nivel_otimizacao, passes))
    warnings = []
    try:
        output = exportar_circuito_qasm(otimizado)
        try:
            qasm2.loads(output, include_path=())
        except qasm2.QASM2Error:
            # The SDK exporter can emit legacy qelib extensions. Lower these to
            # standard gates; report metrics for the circuit actually returned.
            otimizado = transpile(otimizado, basis_gates=["u3", "cx"], optimization_level=0, num_processes=1,
                                  callback=lambda **kw: passes.add(type(kw["pass_"]).__name__))
            output = exportar_circuito_qasm(otimizado)
            qasm2.loads(output, include_path=())
            warnings.append("A saída foi decomposta para portas OpenQASM 2 padrão; as métricas refletem essa decomposição.")
    except ValueError:
        output = ""
        warnings.append("O circuito otimizado não pode ser representado em OpenQASM 2.")
    resultado = avaliar_proposta_ia(original, otimizado)
    if resultado["equivalente"] is False:
        raise CircuitError("EQUIVALENCE_FAILED", "A proposta falhou na verificação de equivalência.", status=500)
    if resultado["motivo"]:
        warnings.append(resultado["motivo"])
    changes = []
    if resultado["diferenca_operacoes"] < 0:
        changes.append("Redução observada na contagem de operações.")
    if resultado["diferenca_profundidade"] < 0:
        changes.append("Redução observada na profundidade.")
    resultado.update(circuito_otimizado_qasm=output, formato_detectado=formato,
                     formato_saida="qasm" if output else None,
                     entrada_sha256=sha256(code.encode("utf-8")).hexdigest(),
                     tempo_processamento_segundos=perf_counter() - started,
                     nivel_otimizacao=nivel_otimizacao, passes_executados=sorted(passes),
                     otimizacoes_aplicadas=changes, warnings=warnings)
    return resultado


def materializar_layout(original, optimized):
    """Restore logical wire order before serializing (QASM has no layout metadata)."""
    if optimized.layout is None:
        return optimized
    n = original.num_qubits
    initial = optimized.layout.initial_index_layout()
    final = optimized.layout.final_index_layout()
    if optimized.num_qubits != n or sorted(initial) != list(range(n)) or sorted(final) != list(range(n)):
        raise CircuitError("UNSUPPORTED_LAYOUT", "Não foi possível exportar o mapeamento de qubits.", status=500)
    inverse = [0] * n
    for logical, physical in enumerate(initial):
        inverse[physical] = logical
    result = original.copy_empty_like()
    result.compose(optimized, qubits=inverse, inplace=True)
    positions = [inverse[p] for p in final]
    for logical in range(n):
        current = positions[logical]
        if current != logical:
            other = positions.index(logical)
            result.swap(logical, current)
            positions[other], positions[logical] = current, logical
    return result


def processar_circuito_qasm(texto_qasm: str, nivel_otimizacao: int = 3):
    return processar_circuito(texto_qasm, "qasm", nivel_otimizacao)


def obter_metricas(circuito: QuantumCircuit):
    """
    Analisa um circuito quântico e retorna
    suas principais métricas.
    """
    validar_circuito(circuito)

    metricas = {
        "qubits": circuito.num_qubits,
        "total_operacoes": circuito.size(),
        "instrucoes": len(circuito.data),
        "bits_classicos": circuito.num_clbits,
        "operacoes_dois_qubits": sum(i.operation.num_qubits == 2 and not getattr(i.operation, "_directive", False)
                                    and not getattr(i.operation, "blocks", ()) for i in circuito.data),
        "profundidade": circuito.depth(),
        "tipos_operacoes": dict(circuito.count_ops())
    }
    return metricas

def otimizar_circuito(circuito:QuantumCircuit, nivel_otimizacao:int=3, passes=None):
    '''
    Recebe um circuito quântico junto com nível
    de otimização, sendo o padrão 3, e faz o processo
    com transpile.
    '''
    validar_circuito(circuito)

    if nivel_otimizacao not in (0, 1, 2, 3):
        raise ValueError("Nível de otimização deve ser 0, 1, 2 ou 3.")

    circuito_otimizado = transpile(
    circuito,
    optimization_level=nivel_otimizacao,
    seed_transpiler=42,
    num_processes=1,
    callback=(lambda **kw: passes.add(type(kw["pass_"]).__name__)) if passes is not None else None,
    )
    return circuito_otimizado

def otimizar_para_hardware(circuito: QuantumCircuit, conectividade: CouplingMap, layout_inicial=None, nivel_otimizacao:int=0, portas_nativas: list[str] | None = None):
    """
    Adapta um circuito quântico às restrições
    de conectividade e portas nativas do hardware.
    """
    validar_circuito(circuito)

    if not isinstance(conectividade, CouplingMap):
        raise TypeError(
            "O parâmetro 'conectividade' deve ser um CouplingMap."
        )

    if nivel_otimizacao not in (0, 1, 2, 3):
        raise ValueError("Nível de otimização deve ser 0, 1, 2 ou 3.")

    otimizado = transpile(
        circuito,
        coupling_map=conectividade,
        basis_gates=portas_nativas,
        initial_layout=layout_inicial,
        layout_method = "sabre" if layout_inicial is None else None,
        routing_method= "basic",
        optimization_level=nivel_otimizacao,
        seed_transpiler=42
    )
    return otimizado

def verificar_equivalencia(original: QuantumCircuit, otimizado: QuantumCircuit, limite_qubits: int = LIM_MAX_EQUIV):
    """
    Verifica equivalência de circuitos unitários,
    considerando os metadados de layout.

    Gera ValueError caso a comparação não
    possa ser realizada.
    """
    validar_circuito(original)
    validar_circuito(otimizado)

    if type(limite_qubits) is not int or limite_qubits < 1:
            raise ValueError(
                "O limite de qubits deve ser um inteiro positivo."
            )

    if limite_qubits > LIM_MAX_EQUIV:
        raise ValueError(
            f"O limite de qubits não pode ultrapassar "
            f"{LIM_MAX_EQUIV}."
        )

    if original.num_qubits != otimizado.num_qubits:
        return False

    if original.num_qubits > limite_qubits:
        raise ValueError(
            f"O circuito possui {original.num_qubits} qubits, "
            f"acima do limite permitido de {limite_qubits}."
        )

    limits = get_limits()
    if original.num_qubits > min(limits.max_equivalence_qubits, LIM_MAX_EQUIV):
        raise ValueError("Equivalência matricial omitida: circuito acima do orçamento de qubits.")
    if max(len(original.data), len(otimizado.data)) > limits.max_equivalence_operations:
        raise ValueError("Equivalência matricial omitida: circuito acima do orçamento de operações.")
    if any(i.operation.name in ("measure", "reset") or getattr(i.operation, "blocks", ())
           for c in (original, otimizado) for i in c.data):
        raise ValueError("Equivalência unitária não se aplica a medições, reset ou controle clássico.")

    try:
        operador_original = Operator.from_circuit(original)
        operador_otimizado = Operator.from_circuit(otimizado)

        return bool(
            operador_original.equiv(operador_otimizado)
        )

    except QiskitError as erro:
        raise ValueError(
            "Não foi possível verificar equivalência pelo método de operadores unitários."
        ) from erro


def comparar_niveis(circuito: QuantumCircuit):
    """
    Compara o circuito original com suas versões
    otimizadas em diferentes níveis de otimização.
    """
    resultados = {}
    nivel = 0

    while nivel <= 3:
        otimizado = otimizar_circuito(circuito, nivel)

        motivo = None

        try:
            equivalente = verificar_equivalencia(
                circuito, otimizado
            )

            if equivalente:
                status = "equivalente"
            else:
                status = "nao_equivalente"

        except ValueError as erro:
            equivalente = None
            status = "nao_verificado"
            motivo = str(erro)

        resultados[nivel] = {
            "metricas": obter_metricas(otimizado),
            "equivalente": equivalente,
            "status_verificacao": status,
            "motivo": motivo
        }

        nivel += 1

    return resultados

def analisar_adaptacao_hardware(original: QuantumCircuit, adaptado: QuantumCircuit, portas_nativas: list[str] | None = None):
    """
    Compara um circuito original com sua versão
    adaptada ao hardware.
    """

    # Obter métricas
    metricas_originais = obter_metricas(original)
    metricas_adaptadas = obter_metricas(adaptado)

    # Calcular diferenças
    diferenca_operacoes = (
        metricas_adaptadas["total_operacoes"]
        - metricas_originais["total_operacoes"]
    )

    diferenca_profundidade = (
        metricas_adaptadas["profundidade"]
        - metricas_originais["profundidade"]
    )

    # Verificar equivalência
    motivo = None

    try:
        equivalente = verificar_equivalencia(
            original, adaptado
        )

        if equivalente:
            status = "equivalente"
        else:
            status = "nao_equivalente"

    except ValueError as erro:
        equivalente = None
        status = "nao_verificado"
        motivo = str(erro)

    # Verificar portas nativas
    compativel_portas = None

    if portas_nativas is not None:
        compativel_portas = set(adaptado.count_ops()).issubset(set(portas_nativas))

    resultados = {
        "original": metricas_originais,
        "adaptado": metricas_adaptadas,
        "diferenca_operacoes": diferenca_operacoes,
        "diferenca_profundidade": diferenca_profundidade,
        "equivalente": equivalente,
        "status_verificacao": status,
        "motivo": motivo,
        "somente_portas_permitidas": compativel_portas
    }

    return resultados

def avaliar_proposta_ia(original: QuantumCircuit, proposta: QuantumCircuit):
    """
    Avalia uma proposta de otimização quântica,
    considerando métricas e equivalência.
    """

    metricas_original = obter_metricas(original)
    metricas_proposta = obter_metricas(proposta)

    diferenca_operacoes = (
        metricas_proposta["total_operacoes"]
        - metricas_original["total_operacoes"]
    )

    diferenca_profundidade = (
        metricas_proposta["profundidade"]
        - metricas_original["profundidade"]
    )

    # Tentar verificar equivalência
    motivo = None

    try:
        equivalente = verificar_equivalencia(
            original, proposta
        )

        if equivalente:
            status = "equivalente"
        else:
            status = "nao_equivalente"

    except ValueError as erro:
        equivalente = None
        status = "nao_verificado"
        motivo = str(erro)

    # Avaliar reduções apenas se houve verificação
    if equivalente is None:
        reduziu_operacoes = None
        reduziu_profundidade = None
    else:
        reduziu_operacoes = (
            equivalente and diferenca_operacoes < 0
        )

        reduziu_profundidade = (
            equivalente and diferenca_profundidade < 0
        )

    resultados = {
        "original": metricas_original,
        "proposta": metricas_proposta,
        "equivalente": equivalente,
        "status_verificacao": status,
        "motivo": motivo,
        "diferenca_operacoes": diferenca_operacoes,
        "diferenca_profundidade": diferenca_profundidade,
        "reduziu_operacoes": reduziu_operacoes,
        "reduziu_profundidade": reduziu_profundidade
    }

    return resultados
