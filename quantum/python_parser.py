"""Interpret a bounded construction subset of Python; never execute source.

Supported: Qiskit circuit/register imports, numeric arithmetic, math constants,
register indexing/slices, lists/tuples/range, assignments, bounded for loops,
``qc.qubits``/``qc.clbits`` and ``with qc.if_test((bit_or_register, value)):`` blocks.
Only explicit circuit gate methods below are callable. No Python object access,
filesystem/network calls, comprehensions, functions, classes or arbitrary imports.
"""
import ast
import math
import operator

from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit import Qubit, Clbit
from qiskit.exceptions import QiskitError

from .config import get_limits, validate_source
from .errors import CircuitError, enforce_limit

CONSTRUCTORS = {"QuantumCircuit": QuantumCircuit, "QuantumRegister": QuantumRegister,
                "ClassicalRegister": ClassicalRegister}
GATES = frozenset("h x y z id s sdg t tdg sx sxdg rx ry rz p u cx cy cz ch cp crx cry crz cu ccx ccz cswap swap rxx ryy rzz rzx ecr dcx iswap reset measure measure_all barrier".split())
MATH = {"pi": math.pi, "tau": math.tau, "e": math.e,
        **{name: getattr(math, name) for name in ("sin", "cos", "tan", "sqrt", "exp", "log")}}
BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}


class Interpreter:
    def __init__(self, limits):
        self.limits = limits
        self.names = {}
        self.steps = 0
        self.circuit = None

    def reject(self, node, reason="Comando fora do subconjunto Python permitido."):
        raise CircuitError("UNSUPPORTED_PYTHON", reason,
                           {"line": getattr(node, "lineno", 1), "reason": reason})

    def reject_import(self, node, name):
        if name in ("qiskit.circuit.Parameter", "qiskit.Parameter"):
            self.reject(node, "Parameter não é suportado nesta análise: use ângulos numéricos "
                        "no circuito enviado. Parâmetros simbólicos ainda não são aceitos.")
        self.reject(node, f"Importação '{name}' não suportada. Envie a construção do circuito "
                    "com QuantumCircuit, QuantumRegister, ClassicalRegister e funções de math; "
                    "não inclua execução, simulação ou visualização.")

    def tick(self):
        self.steps += 1
        enforce_limit("python_steps", self.steps, self.limits.max_python_steps)

    def number(self, value, node):
        if type(value) not in (int, float):
            self.reject(node, "A expressão requer números, sem operações sobre objetos ou listas.")
        if not math.isfinite(value) or abs(value) > 1e12:
            self.reject(node, "Número fora da faixa permitida (finito, módulo <= 10^12).")
        return value

    def expression(self, node):
        self.tick()
        if isinstance(node, ast.Constant):
            if type(node.value) in (int, float):
                return self.number(node.value, node)
            if isinstance(node.value, str) and len(node.value) <= 100:
                return node.value
            self.reject(node)
        if isinstance(node, ast.Name) and node.id in self.names:
            return self.names[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            enforce_limit("python_collection", len(node.elts), self.limits.max_circuit_gates)
            return [self.expression(item) for item in node.elts]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.number(self.expression(node.operand), node)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            left = self.number(self.expression(node.left), node)
            right = self.number(self.expression(node.right), node)
            return self.number(BINARY[type(node.op)](left, right), node)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and self.names.get(node.value.id) == "math":
                if node.attr in MATH:
                    return MATH[node.attr]
            if isinstance(node.value, ast.Name) and self.names.get(node.value.id) == "qiskit":
                if node.attr in CONSTRUCTORS:
                    return CONSTRUCTORS[node.attr]
            if isinstance(node.value, ast.Name) and isinstance(self.names.get(node.value.id), QuantumCircuit)                     and node.attr in ("qubits", "clbits"):
                return list(getattr(self.names[node.value.id], node.attr))
            self.reject(node, "Acesso a atributos não permitido.")
        if isinstance(node, ast.Subscript):
            value = self.expression(node.value)
            if not isinstance(value, (QuantumRegister, ClassicalRegister, list, tuple, range)):
                self.reject(node)
            if isinstance(node.slice, ast.Slice):
                parts = [self.expression(n) if n else None for n in (node.slice.lower, node.slice.upper, node.slice.step)]
                if any(p is not None and type(p) is not int for p in parts):
                    self.reject(node)
                return value[slice(*parts)]
            index = self.expression(node.slice)
            if type(index) is not int:
                self.reject(node)
            return value[index]
        if isinstance(node, ast.Call):
            return self.call(node)
        self.reject(node)

    def call(self, node):
        if any(k.arg is None for k in node.keywords):
            self.reject(node)
        args = [self.expression(a) for a in node.args]
        kwargs = {k.arg: self.expression(k.value) for k in node.keywords}
        if isinstance(node.func, ast.Name) and node.func.id == "range":
            if kwargs or not 1 <= len(args) <= 3 or any(type(a) is not int for a in args):
                self.reject(node)
            result = range(*args)
            enforce_limit("python_collection", len(result), self.limits.max_python_steps)
            return result
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            receiver = self.names.get(node.func.value.id)
            if isinstance(receiver, QuantumCircuit):
                if node.func.attr not in GATES:
                    self.reject(node, "Método de circuito não permitido.")
                # Only numeric angles, bits and bounded flat collections reach Qiskit.
                def valid(arg):
                    if type(arg) in (int, float) or isinstance(arg, (Qubit, Clbit, QuantumRegister, ClassicalRegister)):
                        return True
                    return isinstance(arg, (list, tuple, range)) and all(type(x) in (int, float) or isinstance(x, (Qubit, Clbit)) for x in arg)
                if not all(valid(a) for a in args) or kwargs:
                    self.reject(node, "Portas aceitam apenas argumentos posicionais numéricos, bits ou registradores.")
                # Bound broadcast work before allocating instructions.
                widths = [len(a) for a in args if isinstance(a, (list, tuple, range, QuantumRegister, ClassicalRegister))]
                width = max(widths, default=receiver.num_qubits if node.func.attr == "measure_all" else 1)
                enforce_limit("circuit_gates", len(receiver.data) + width, self.limits.max_circuit_gates)
                getattr(receiver, node.func.attr)(*args)
                enforce_limit("circuit_gates", len(receiver.data), self.limits.max_circuit_gates)
                enforce_limit("classical_bits", receiver.num_clbits, self.limits.max_classical_bits)
                return None
        target = self.expression(node.func)
        if any(target is constructor for constructor in CONSTRUCTORS.values()):
            if set(kwargs) - {"name"} or ("name" in kwargs and not isinstance(kwargs["name"], str)):
                self.reject(node)
            if target is QuantumCircuit:
                if self.circuit is not None:
                    self.reject(node, "Construa um único QuantumCircuit por entrada.")
                if all(type(a) is int for a in args) and 1 <= len(args) <= 2:
                    nq, nc = args[0], args[1] if len(args) == 2 else 0
                elif args and all(isinstance(a, (QuantumRegister, ClassicalRegister)) for a in args):
                    nq = sum(len(a) for a in args if isinstance(a, QuantumRegister))
                    nc = sum(len(a) for a in args if isinstance(a, ClassicalRegister))
                else:
                    self.reject(node, "Use QuantumCircuit(qubits, bits) ou registradores explícitos.")
                enforce_limit("qubits", nq, self.limits.max_qubits)
                enforce_limit("classical_bits", nc, self.limits.max_classical_bits)
                self.circuit = target(*args, **kwargs)
                return self.circuit
            if not 1 <= len(args) <= 2 or type(args[0]) is not int or (len(args) == 2 and not isinstance(args[1], str)):
                self.reject(node)
            maximum = self.limits.max_qubits if target is QuantumRegister else self.limits.max_classical_bits
            enforce_limit("register_bits", args[0], maximum)
            return target(*args, **kwargs)
        if any(target is value for value in MATH.values() if callable(value)) and not kwargs:
            return self.number(target(*(self.number(a, node) for a in args)), node)
        self.reject(node, "Chamada de função não permitida.")

    def statements(self, statements):
        for node in statements:
            self.tick()
            try:
                if isinstance(node, ast.ImportFrom):
                    if node.level != 0 or node.module not in ("qiskit", "qiskit.circuit", "math"):
                        self.reject_import(node, f"{'.' * node.level}{node.module or ''}.{node.names[0].name}")
                    allowed = MATH if node.module == "math" else CONSTRUCTORS
                    for alias in node.names:
                        if alias.name not in allowed:
                            self.reject_import(node, f"{node.module}.{alias.name}")
                        self.names[alias.asname or alias.name] = allowed[alias.name]
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in ("math", "qiskit"):
                            self.reject_import(node, alias.name)
                        self.names[alias.asname or alias.name] = alias.name
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    self.names[node.targets[0].id] = self.expression(node.value)
                elif isinstance(node, ast.Expr):
                    self.expression(node.value)
                elif isinstance(node, ast.With) and len(node.items) == 1 and node.items[0].optional_vars is None:
                    self.if_test(node)
                elif isinstance(node, ast.For) and isinstance(node.target, ast.Name) and not node.orelse:
                    items = self.expression(node.iter)
                    if not isinstance(items, (list, tuple, range)):
                        self.reject(node)
                    for item in items:
                        self.tick()
                        self.names[node.target.id] = item
                        self.statements(node.body)
                else:
                    self.reject(node)
            except CircuitError:
                raise
            except (ValueError, TypeError, IndexError, ZeroDivisionError, OverflowError, QiskitError) as exc:
                raise CircuitError("INVALID_CIRCUIT", "Comando Python/Qiskit inválido.",
                                   {"line": node.lineno, "reason": str(exc)[:500]}) from exc


def _if_test(self, node):
    """with qc.if_test((Clbit | ClassicalRegister, int)): corpo"""
    call = node.items[0].context_expr
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "if_test"
            and isinstance(call.func.value, ast.Name) and isinstance(self.names.get(call.func.value.id), QuantumCircuit)
            and len(call.args) == 1 and not call.keywords):
        self.reject(node, "Somente blocos 'with qc.if_test((bit, valor)):' são aceitos.")
    condicao = self.expression(call.args[0])
    if not (isinstance(condicao, list) and len(condicao) == 2 and isinstance(condicao[0], (Clbit, ClassicalRegister))
            and type(condicao[1]) is int):
        self.reject(node, "A condição de if_test deve ser (bit clássico ou registrador, inteiro).")
    with self.names[call.func.value.id].if_test(tuple(condicao)):
        self.statements(node.body)


Interpreter.if_test = _if_test


def carregar_circuito_python(code):
    limits = validate_source(code, get_limits())
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError) as exc:
        raise CircuitError("INVALID_CIRCUIT", "Sintaxe Python inválida.",
                           {"line": getattr(exc, "lineno", None) or 1, "reason": str(exc)[:500]}) from exc
    pending = [(tree, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        enforce_limit("python_ast_nodes", count, limits.max_python_steps)
        enforce_limit("python_ast_depth", depth, 80)
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    interpreter = Interpreter(limits)
    interpreter.statements(tree.body)
    if interpreter.circuit is None:
        raise CircuitError("INVALID_CIRCUIT", "Nenhum QuantumCircuit foi construído.")
    return interpreter.circuit
