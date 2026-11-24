"""Análise estática de código Python/Qiskit, tolerante a erros. Nunca executa o código.

Detecta, sem executar:
- erros de sintaxe (inclusive vários, isolando a linha problemática);
- imports ausentes, imports de APIs removidas e métodos antigos de QuantumCircuit;
- índices de qubits/bits fora do circuito, qubits repetidos, medição sem bits,
  portas inexistentes e número errado de argumentos;
- uso de Parameter sem atribuição de valores.

Correções inequívocas (renomear método, trocar import) são aplicadas; as
ambíguas trazem alternativas e seguem a política escolhida.
"""
import ast
import difflib
import io
import re
import tokenize

from qiskit import QuantumCircuit

from .migracao import REGRAS

# Portas com assinatura fixa: nome -> (ângulos, qubits).
ASSINATURAS = {
    **{g: (0, 1) for g in "h x y z id s sdg t tdg sx sxdg".split()},
    **{g: (1, 1) for g in "rx ry rz p".split()}, "u": (3, 1), "r": (2, 1),
    **{g: (0, 2) for g in "cx cy cz ch swap dcx ecr iswap csx".split()},
    **{g: (1, 2) for g in "cp crx cry crz rxx ryy rzz rzx".split()}, "cu": (4, 2),
    **{g: (0, 3) for g in "ccx ccz cswap rccx".split()}, "rcccx": (0, 4),
}
CONSTRUTORES = {"QuantumCircuit": "qiskit", "QuantumRegister": "qiskit", "ClassicalRegister": "qiskit",
                "transpile": "qiskit", "Parameter": "qiskit.circuit", "ParameterVector": "qiskit.circuit",
                "Statevector": "qiskit.quantum_info", "Operator": "qiskit.quantum_info",
                "SparsePauliOp": "qiskit.quantum_info", "AerSimulator": "qiskit_aer",
                "StatevectorSampler": "qiskit.primitives", "StatevectorEstimator": "qiskit.primitives",
                "generate_preset_pass_manager": "qiskit.transpiler", "plot_histogram": "qiskit.visualization",
                "qasm2": "qiskit", "qasm3": "qiskit"}
METODOS_RENOMEADOS = {r["metodo"][0]: (r["metodo"][1], r) for r in REGRAS if r.get("metodo")}
METODOS_VALIDOS = {n for n in dir(QuantumCircuit) if not n.startswith("_")}


def _primeiro_erro_sintaxe(linhas):
    try:
        ast.parse("\n".join(linhas))
        return None
    except SyntaxError as erro:
        return erro


def analisar_sintaxe(codigo):
    """Encontra vários erros de sintaxe isolando cada linha problemática."""
    linhas = codigo.split("\n")
    erros, tentativas = [], 0
    trabalho = list(linhas)
    while tentativas < 20:
        erro = _primeiro_erro_sintaxe(trabalho)
        if erro is None:
            break
        n = (erro.lineno or 1) - 1
        if n >= len(trabalho):
            n = len(trabalho) - 1
        texto = linhas[n] if n < len(linhas) else ""
        correcao = None
        m = re.fullmatch(r"(\s*)print\s+(.+?)\s*", texto)
        if m and "(" not in texto.split("print", 1)[1][:2]:
            correcao = f"{m.group(1)}print({m.group(2)})"
        elif re.match(r"\s*(for|if|elif|else|while|def|class|with|try|except)\b.*[^:]\s*$", texto):
            correcao = texto.rstrip() + ":"
        elif texto.count("(") > texto.count(")"):
            correcao = texto.rstrip() + ")" * (texto.count("(") - texto.count(")"))
        tentativas += 1
        if any(e["linha"] == n + 1 for e in erros):
            # O mesmo erro reapareceu: a linha já foi neutralizada; remove a indentação.
            if trabalho[n].strip() and trabalho[n] != trabalho[n].lstrip():
                trabalho[n] = trabalho[n].lstrip()
                continue
            break
        erros.append({"linha": n + 1, "coluna": erro.offset, "mensagem": erro.msg, "texto": texto, "correcao": correcao})
        # Continua a análise com a correção proposta ou neutralizando a linha (mantém a numeração).
        indent = re.match(r"\s*", trabalho[n]).group(0)
        trabalho[n] = correcao if correcao else (indent + "pass" if trabalho[n].strip() else trabalho[n])
    return erros, trabalho


class _Rastreador(ast.NodeVisitor):
    """Acompanha circuitos construídos com tamanhos literais e valida chamadas."""

    def __init__(self, problema, linhas):
        self.problema = problema
        self.linhas = linhas
        self.circuitos = {}      # nome -> {"qubits": n, "bits": m, "linha": l}
        self.registros = {}      # nome -> ("q"|"c", tamanho)
        self.constantes = {}
        self.parametros = set()
        self.atribuidos = False
        self.importados = set()
        self.usados = {}
        self.renomes = {}        # linha -> [(antigo, novo)]
        self.remover_linhas = set()

    def visit_Import(self, node):
        for alias in node.names:
            self.importados.add((alias.asname or alias.name).split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.importados.add(alias.asname or alias.name)

    def _int(self, no):
        if isinstance(no, ast.Constant) and type(no.value) is int:
            return no.value
        if isinstance(no, ast.Name) and type(self.constantes.get(no.id)) is int:
            return self.constantes[no.id]
        if isinstance(no, ast.UnaryOp) and isinstance(no.op, ast.USub):
            v = self._int(no.operand)
            return -v if v is not None else None
        return None

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            nome = node.targets[0].id
            valor = node.value
            if isinstance(valor, ast.Constant) and type(valor.value) in (int, float):
                self.constantes[nome] = valor.value
            if isinstance(valor, ast.Call) and isinstance(valor.func, ast.Name):
                func = valor.func.id
                args = [self._int(a) for a in valor.args]
                if func == "QuantumCircuit":
                    if valor.args and all(a is not None for a in args) and 1 <= len(args) <= 2:
                        self.circuitos[nome] = {"qubits": args[0], "bits": args[1] if len(args) > 1 else 0,
                                                "linha": node.lineno}
                    elif valor.args and all(isinstance(a, ast.Name) and a.id in self.registros for a in valor.args):
                        regs = [self.registros[a.id] for a in valor.args]
                        self.circuitos[nome] = {"qubits": sum(n for k, n in regs if k == "q"),
                                                "bits": sum(n for k, n in regs if k == "c"), "linha": node.lineno}
                elif func in ("QuantumRegister", "ClassicalRegister") and args and args[0] is not None:
                    self.registros[nome] = ("q" if func == "QuantumRegister" else "c", args[0])
                elif func == "Parameter":
                    self.parametros.add(nome)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.usados.setdefault(node.id, node.lineno)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            alvo, metodo = func.value.id, func.attr
            if metodo in ("assign_parameters", "bind_parameters"):
                self.atribuidos = True
            if alvo in self.circuitos:
                self._checar_chamada(node, self.circuitos[alvo], alvo, metodo)
        self.generic_visit(node)

    def _qubits_de(self, no):
        if isinstance(no, ast.Subscript) and isinstance(no.value, ast.Name) and no.value.id in self.registros:
            return None  # índice relativo ao registrador; validado como subscript
        return self._int(no)

    def _checar_chamada(self, node, circ, alvo, metodo):
        linha = node.lineno
        if metodo in METODOS_RENOMEADOS:
            novo, regra = METODOS_RENOMEADOS[metodo]
            self.problema("REMOVED_METHOD", linha, f"{alvo}.{metodo}() não existe no Qiskit 2.x.",
                          f"{regra['antigo']} foi removido (versão {regra['removido_em']}).",
                          {"tipo": "substituicao", "descricao": f"Usar {alvo}.{novo}()."}, confianca="alta")
            self.renomes.setdefault(linha, []).append((f"{alvo}.{metodo}(", f"{alvo}.{novo}("))
            metodo = novo
        elif metodo not in METODOS_VALIDOS:
            sugestao = difflib.get_close_matches(metodo, sorted(METODOS_VALIDOS), 1, 0.75)
            self.problema("UNKNOWN_METHOD", linha, f"QuantumCircuit não tem o método '{metodo}'.",
                          "O nome não corresponde a nenhuma porta ou método do QuantumCircuit.",
                          {"tipo": "remocao", "descricao": "Remover a chamada (intenção desconhecida)."},
                          [f"Talvez seja {alvo}.{sugestao[0]}()." if sugestao else "Consulte a lista de portas do QuantumCircuit."],
                          confianca="media" if sugestao else "baixa")
            self.remover_linhas.add(linha)
            return
        n_q, n_c = circ["qubits"], circ["bits"]
        args = node.args
        if metodo in ASSINATURAS:
            n_ang, n_qub = ASSINATURAS[metodo]
            if len(args) != n_ang + n_qub:
                self.problema("WRONG_ARGUMENT_COUNT", linha,
                              f"{alvo}.{metodo} espera {n_ang} ângulo(s) e {n_qub} qubit(s); recebeu {len(args)} argumento(s).",
                              "A quantidade de argumentos não corresponde à porta; valores ausentes não podem ser inferidos.",
                              {"tipo": "remocao", "descricao": "Remover a chamada."},
                              [f"Completar a chamada: {alvo}.{metodo}({', '.join(['angulo'] * n_ang + ['qubit'] * n_qub)})."],
                              confianca="baixa")
                self.remover_linhas.add(linha)
                return
            for a in args[:n_ang]:
                if isinstance(a, ast.Name) and a.id in self.parametros:
                    continue
            indices = [self._qubits_de(a) for a in args[n_ang:]]
            self._checar_indices(linha, alvo, metodo, indices, n_q, "qubit")
            concretos = [i for i in indices if i is not None]
            if n_qub > 1 and len(concretos) != len(set(concretos)):
                self.problema("DUPLICATE_QUBIT", linha, f"{alvo}.{metodo} usa o mesmo qubit mais de uma vez.",
                              "Portas de vários qubits exigem qubits distintos.",
                              {"tipo": "remocao", "descricao": "Remover a chamada (qubit pretendido desconhecido)."},
                              ["Trocar um dos índices pelo qubit pretendido."], confianca="baixa")
                self.remover_linhas.add(linha)
        elif metodo == "measure":
            if len(args) != 2:
                return
            if n_c == 0:
                self.problema("MEASURE_WITHOUT_CLBITS", linha, f"{alvo} não tem bits clássicos para receber a medição.",
                              "QuantumCircuit(n) sem segundo argumento não cria bits clássicos.",
                              {"tipo": "substituicao", "descricao": f"Criar o circuito com bits: QuantumCircuit({n_q}, {n_q})."},
                              [f"Usar {alvo}.measure_all() no lugar das medições individuais."], confianca="media")
                self.redimensionar = (circ["linha"], n_q)
                return
            self._checar_indices(linha, alvo, metodo, [self._int(args[0])], n_q, "qubit")
            self._checar_indices(linha, alvo, metodo, [self._int(args[1])], n_c, "bit clássico")
        elif metodo in ("reset", "barrier") and args:
            self._checar_indices(linha, alvo, metodo, [self._int(a) for a in args], n_q, "qubit")

    def _checar_indices(self, linha, alvo, metodo, indices, limite, rotulo):
        for indice in indices:
            if indice is not None and not 0 <= indice < limite:
                self.problema("INDEX_OUT_OF_RANGE", linha,
                              f"{alvo}.{metodo}: {rotulo} {indice} não existe ({alvo} tem {limite}, índices 0 a {limite - 1}).",
                              f"O índice está fora do número de {rotulo}s declarado em QuantumCircuit.",
                              {"tipo": "remocao", "descricao": "Remover a chamada (índice pretendido desconhecido)."},
                              [f"Aumentar o circuito para {indice + 1} {rotulo}s se faltou declará-los.",
                               f"Trocar o índice por um valor entre 0 e {limite - 1} se foi erro de digitação."],
                              confianca="baixa")
                self.remover_linhas.add(linha)
                return


def analisar(codigo: str, politica: str = "conservadora") -> dict:
    problemas = []

    def problema(codigo_erro, linha, mensagem, causa, correcao, alternativas=(), confianca="alta",
                 severidade="erro", categoria="correcao_de_codigo"):
        problemas.append({"codigo": codigo_erro, "severidade": severidade, "linha": linha,
                          "trecho": linhas[linha - 1].strip()[:200] if 0 < linha <= len(linhas) else "",
                          "mensagem": mensagem, "causa_raiz": causa, "correcao": correcao,
                          "alternativas": list(alternativas), "confianca": confianca, "categoria": categoria})

    linhas = codigo.split("\n")
    erros_sintaxe, trabalho = analisar_sintaxe(codigo)
    corrigidas = list(linhas)
    for e in erros_sintaxe:
        problema("SYNTAX_ERROR", e["linha"], f"Erro de sintaxe Python: {e['mensagem']}.",
                 "O código não segue a gramática do Python; nenhuma linha após o erro seria executada.",
                 {"tipo": "substituicao" if e["correcao"] else "remocao",
                  "descricao": f"Reescrever a linha como: {e['correcao']}" if e["correcao"] else
                  "Remover/reescrever a linha (correção não determinável automaticamente)."},
                 [] if e["correcao"] else ["Revisar parênteses, dois-pontos e indentação da linha."],
                 confianca="media" if e["correcao"] else "baixa")
        corrigidas[e["linha"] - 1] = e["correcao"] if e["correcao"] else None
    try:
        arvore = ast.parse("\n".join(trabalho))
    except SyntaxError:
        arvore = None

    # Regras de migração por padrão de texto (imports e chamadas removidas).
    imports_novos = []
    for numero, (linha, codigo_linha) in enumerate(zip(linhas, _somente_codigo(linhas)), start=1):
        for regra in REGRAS:
            if regra.get("metodo") or regra.get("aviso_apenas"):
                continue
            if re.search(regra["padrao"], codigo_linha):
                problema("REMOVED_API", numero, f"{regra['antigo']} não existe no Qiskit 2.x.",
                         f"API removida (versão {regra['removido_em']}). Substituto: {regra['novo']}.",
                         {"tipo": "manual", "descricao": f"Substituir por {regra['novo']}."},
                         confianca="alta", categoria="migracao_de_versao")
                nova = _reescrever_migracao(regra["id"], linha, imports_novos)
                if nova is not None and corrigidas[numero - 1] is not None:
                    corrigidas[numero - 1] = nova
                    problemas[-1]["correcao"] = {"tipo": "substituicao", "descricao": f"Reescrito: {nova.strip()}"}

    rastreador = _Rastreador(problema, linhas)
    if arvore is not None:
        rastreador.visit(arvore)
        for nome, linha in sorted(rastreador.usados.items(), key=lambda x: x[1]):
            if nome in CONSTRUTORES and nome not in rastreador.importados and not _definido(arvore, nome):
                modulo = CONSTRUTORES[nome]
                problema("MISSING_IMPORT", linha, f"'{nome}' é usado sem import.",
                         f"'{nome}' pertence a {modulo}; sem o import ocorre NameError.",
                         {"tipo": "insercao", "descricao": f"Adicionar 'from {modulo} import {nome}'."})
                imports_novos.append(f"from {modulo} import {nome}")
        if rastreador.parametros and not rastreador.atribuidos:
            problema("UNBOUND_PARAMETERS", min(rastreador.usados.get(p, 1) for p in rastreador.parametros),
                     f"Parâmetros sem valor: {', '.join(sorted(rastreador.parametros))}.",
                     "Circuitos com Parameter precisam de valores (assign_parameters ou PUB das primitivas) antes de simular.",
                     {"tipo": "nenhuma", "descricao": "Atribua valores com qc.assign_parameters({...})."},
                     ["Enviar os valores na PUB: sampler.run([(qc, valores)])."], confianca="alta", severidade="aviso")
    for linha, trocas in rastreador.renomes.items():
        if corrigidas[linha - 1] is not None:
            for antigo, novo in trocas:
                corrigidas[linha - 1] = corrigidas[linha - 1].replace(antigo, novo)
    for linha in rastreador.remover_linhas:
        corrigidas[linha - 1] = None
    redim = getattr(rastreador, "redimensionar", None)
    if redim and corrigidas[redim[0] - 1] is not None:
        corrigidas[redim[0] - 1] = re.sub(r"QuantumCircuit\(\s*(\w+)\s*\)", r"QuantumCircuit(\1, \1)", corrigidas[redim[0] - 1])
    saida = [l for l in corrigidas if l is not None]
    for imp in reversed(list(dict.fromkeys(imports_novos))):
        if imp not in saida:
            posicao = max((k + 1 for k, l in enumerate(saida) if re.match(r"(from|import)\s", l)), default=0)
            saida.insert(posicao, imp)
    return {"problemas": problemas, "codigo_corrigido": "\n".join(saida).rstrip() + "\n",
            "circuitos": rastreador.circuitos}


_TOKENS_TEXTO = {tokenize.COMMENT, tokenize.STRING} | (
    {tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())


def _somente_codigo(linhas):
    """Cada linha sem comentários e com strings esvaziadas, para as regras de texto não casarem
    com ``# antes: execute(...)`` ou mensagens. Se o código não tokenizar (quebrado), corta só o '#'
    de linhas sem aspas."""
    limpas = list(linhas)
    try:
        for tok in tokenize.generate_tokens(io.StringIO("\n".join(linhas) + "\n").readline):
            if tok.type not in _TOKENS_TEXTO:
                continue
            (l1, c1), (l2, c2) = tok.start, tok.end
            if l1 != l2 or l1 > len(limpas):
                continue  # strings de várias linhas: mantém (raro em construção de circuito)
            atual = limpas[l1 - 1]
            # preserva o comprimento para que as colunas dos tokens seguintes continuem válidas
            limpas[l1 - 1] = atual[:c1] + " " * (c2 - c1) + atual[c2:]
        return limpas
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [l.split("#", 1)[0] if "#" in l and not re.search(r"['\"]", l.split("#", 1)[0]) else l for l in linhas]


def _definido(arvore, nome):
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.ClassDef)) and no.name == nome:
            return True
        if isinstance(no, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nome for t in no.targets):
            return True
    return False


def _reescrever_migracao(regra, linha, imports_novos):
    """Reescritas seguras e locais; retorna None quando exigem revisão humana."""
    if regra == "execute":
        # execute(qc, backend[, shots=N]) em qualquer posição da linha (atribuição, print(...), encadeado)
        padrao = r"(?<![\w.])execute\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*shots\s*=\s*(\w+))?\s*\)"
        if re.search(padrao, linha):
            imports_novos.append("from qiskit import transpile")
            return re.sub(padrao, lambda m: f"{m.group(2)}.run(transpile({m.group(1)}, {m.group(2)})"
                          + (f", shots={m.group(3)}" if m.group(3) else "") + ")", linha)
        if re.match(r"\s*from\s+qiskit\s+import\s+", linha):
            nomes = [n.strip() for n in linha.split("import", 1)[1].strip(" ()").split(",")]
            restantes = [n for n in nomes if n not in ("execute", "Aer", "BasicAer", "IBMQ", "assemble")]
            return re.match(r"\s*", linha).group(0) + ("from qiskit import " + ", ".join(restantes) if restantes else "")
    if regra == "aer_no_qiskit":
        if re.match(r"\s*from\s+qiskit\s+import\s+", linha):
            nomes = [n.strip() for n in linha.split("import", 1)[1].strip(" ()").split(",")]
            restantes = [n for n in nomes if n not in ("execute", "Aer", "BasicAer", "IBMQ", "assemble")]
            imports_novos.append("from qiskit_aer import AerSimulator")
            return re.match(r"\s*", linha).group(0) + ("from qiskit import " + ", ".join(restantes) if restantes else "")
        m = re.match(r"(\s*)(\w+)\s*=\s*Aer\.get_backend\(\s*['\"](\w+)['\"]\s*\)\s*$", linha)
        if m:
            imports_novos.append("from qiskit_aer import AerSimulator")
            metodo = {"statevector_simulator": "statevector", "unitary_simulator": "unitary"}.get(m.group(3))
            argumentos = f'method="{metodo}"' if metodo else ""
            return f"{m.group(1)}{m.group(2)} = AerSimulator({argumentos})"
    if regra == "c_if":
        # qc.x(1).c_if(0, 1)  ->  with qc.if_test((qc.clbits[0], 1)):\n    qc.x(1)
        m = re.match(r"(\s*)(\w+)\.(\w+\(.*\))\.c_if\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*$", linha)
        if m:
            indent, circ, chamada, alvo, valor = m.groups()
            condicao = f"{circ}.clbits[{alvo}]" if alvo.isdigit() else alvo
            return f"{indent}with {circ}.if_test(({condicao}, {valor})):\n{indent}    {circ}.{chamada}"
    if regra == "qasm_metodo":
        m = re.search(r"(\w+)\.qasm\(\s*\)", linha)
        if m:
            imports_novos.append("from qiskit import qasm2")
            return linha.replace(m.group(0), f"qasm2.dumps({m.group(1)})")
    return None
