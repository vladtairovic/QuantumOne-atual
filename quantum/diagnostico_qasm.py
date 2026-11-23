"""Análise tolerante a erros de OpenQASM 2.0 (e conversão do subconjunto básico de 3.0).

Diferente do parser do Qiskit, que para no primeiro erro, este analisador
percorre todas as instruções, acumula os problemas e propõe correções.

Política de correção:
- Correções inequívocas são aplicadas automaticamente (cabeçalho, include,
  ponto e vírgula, nomes antigos de porta, sintaxe OpenQASM 3 equivalente).
- Quando a intenção não pode ser determinada (índice inexistente, ângulo
  ausente, porta desconhecida), o problema traz alternativas e a versão
  corrigida segue a política escolhida: ``conservadora`` remove a instrução;
  ``expandir_registradores`` aumenta o registrador para caber o índice.
Nenhum ângulo, porta ou qubit é inventado.
"""
import ast
import difflib
import math
import re

# Portas de qelib1.inc: nome -> (parâmetros, qubits). Conferido em tests/test_diagnostico.py.
QELIB1 = {
    "u3": (3, 1), "u2": (2, 1), "u1": (1, 1), "u0": (1, 1), "u": (3, 1), "p": (1, 1), "cx": (0, 2), "id": (0, 1),
    "x": (0, 1), "y": (0, 1), "z": (0, 1), "h": (0, 1), "s": (0, 1), "sdg": (0, 1), "t": (0, 1), "tdg": (0, 1),
    "sx": (0, 1), "sxdg": (0, 1), "rx": (1, 1), "ry": (1, 1), "rz": (1, 1), "cz": (0, 2), "cy": (0, 2),
    "ch": (0, 2), "swap": (0, 2), "ccx": (0, 3), "cswap": (0, 3), "crx": (1, 2), "cry": (1, 2), "crz": (1, 2),
    "cu1": (1, 2), "cp": (1, 2), "cu3": (3, 2), "csx": (0, 2), "cu": (4, 2), "rxx": (1, 2), "rzz": (1, 2),
    "rccx": (0, 3), "rc3x": (0, 4), "c3x": (0, 4), "c3sqrtx": (0, 4), "c4x": (0, 5),
}
BUILTIN = {"U": (3, 1), "CX": (0, 2)}
SINONIMOS = {"cnot": "cx", "toffoli": "ccx", "fredkin": "cswap", "i": "id", "phase": "p", "cphase": "cp",
             "not": "x", "hadamard": "h", "ccnot": "ccx", "sdag": "sdg", "tdag": "tdg", "iden": "id"}
FUNCOES = {"sin", "cos", "tan", "exp", "ln", "sqrt", "asin", "acos", "atan"}

_REF = re.compile(r"^([A-Za-z_]\w*)(?:\[(\s*-?\d+\s*)\])?$")


def _sem_comentarios(texto):
    return re.sub(r'"[^"]*"|//[^\n]*|/\*.*?\*/',
                  lambda m: m.group() if m.group().startswith('"') else " " * len(m.group().split("\n")[0])
                  + "\n" * m.group().count("\n"), texto, flags=re.DOTALL)


def _expressao_valida(expr, parametros=()):
    """Valida expressões de ângulo do OpenQASM 2 sem executar código."""
    texto = expr.strip().replace("^", "**")
    if not texto:
        return False, "expressão vazia"
    try:
        arvore = ast.parse(texto, mode="eval")
    except SyntaxError:
        return False, f"expressão inválida: {expr.strip()}"
    for no in ast.walk(arvore):
        if isinstance(no, ast.Name) and no.id not in ("pi", *FUNCOES, *parametros):
            return False, f"identificador '{no.id}' não definido"
        if isinstance(no, ast.Call) and not (isinstance(no.func, ast.Name) and no.func.id in FUNCOES):
            return False, "chamada não permitida"
        if not isinstance(no, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call, ast.Load,
                               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)):
            return False, f"construção não permitida em ângulo: {expr.strip()}"
    return True, None


def _dividir_args(texto):
    partes, nivel, atual = [], 0, ""
    for c in texto:
        if c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
        if c == "," and nivel == 0:
            partes.append(atual.strip())
            atual = ""
        else:
            atual += c
    if atual.strip():
        partes.append(atual.strip())
    return partes


class Instrucao:
    def __init__(self, texto, linha):
        self.texto = texto.strip()
        self.linha = linha
        self.acao = "manter"   # manter | substituir | remover
        self.nova = None


def _instrucoes(limpo):
    """Divide em instruções terminadas por ';' ou blocos de 'gate', com a linha inicial."""
    itens, atual, linha_inicio, linha, profundidade = [], "", None, 1, 0
    for c in limpo:
        if linha_inicio is None and not c.isspace():
            linha_inicio = linha
        atual += c
        if c == "\n":
            linha += 1
        elif c == "{":
            profundidade += 1
        elif c == "}":
            profundidade -= 1
            if profundidade == 0 and re.match(r"\s*(gate|opaque)\b", atual):
                itens.append(Instrucao(atual, linha_inicio))
                atual, linha_inicio = "", None
        elif c == ";" and profundidade == 0:
            itens.append(Instrucao(atual, linha_inicio))
            atual, linha_inicio = "", None
    if atual.strip():
        resto = Instrucao(atual, linha_inicio)
        resto.sem_ponto_virgula = True
        itens.append(resto)
    return itens


def analisar(codigo: str, politica: str = "conservadora") -> dict:
    problemas = []

    def problema(codigo_erro, inst, mensagem, causa, correcao, alternativas=(), confianca="alta",
                 severidade="erro", categoria="correcao_de_codigo"):
        problemas.append({"codigo": codigo_erro, "severidade": severidade, "linha": inst.linha if inst else 1,
                          "trecho": (inst.texto if inst else "")[:200], "mensagem": mensagem, "causa_raiz": causa,
                          "correcao": correcao, "alternativas": list(alternativas), "confianca": confianca,
                          "categoria": categoria})

    limpo = _sem_comentarios(codigo)
    insts = _instrucoes(limpo)
    inseridas_topo = []
    converter_qasm3 = False

    # Cabeçalho e versão.
    cabecalho = next((i for i in insts if re.match(r"OPENQASM\b", i.texto)), None)
    if cabecalho is None:
        inseridas_topo.append("OPENQASM 2.0;")
        problema("MISSING_HEADER", None, "Falta a declaração de versão 'OPENQASM 2.0;'.",
                 "Todo programa OpenQASM começa declarando a versão da linguagem.",
                 {"tipo": "insercao", "descricao": "Inserir 'OPENQASM 2.0;' na primeira linha."})
    elif re.match(r"OPENQASM\s+3", cabecalho.texto):
        converter_qasm3 = True
        cabecalho.acao, cabecalho.nova = "substituir", "OPENQASM 2.0;"
        problema("QASM3_CONVERTIDO", cabecalho, "Programa OpenQASM 3: o subconjunto básico foi convertido para "
                 "OpenQASM 2.0 para análise e otimização.",
                 "O motor do QuantumOne analisa OpenQASM 2.0; declarações, portas e medições de OpenQASM 3 têm "
                 "equivalente direto em 2.0.",
                 {"tipo": "conversao", "descricao": "Converter qubit/bit, include e medições para a sintaxe 2.0."},
                 ["Manter OpenQASM 3 e analisar com qiskit.qasm3 (requer o pacote qiskit-qasm3-import)."],
                 severidade="aviso", categoria="conversao_de_linguagem")
    elif not re.fullmatch(r"OPENQASM\s+2(\.0)?\s*;", cabecalho.texto):
        cabecalho.acao, cabecalho.nova = "substituir", "OPENQASM 2.0;"
        problema("INVALID_HEADER", cabecalho, f"Declaração de versão inválida: {cabecalho.texto}",
                 "A versão deve ser escrita como 'OPENQASM 2.0;'.",
                 {"tipo": "substituicao", "descricao": "Usar 'OPENQASM 2.0;'."})

    registros = {"qreg": {}, "creg": {}}
    portas = dict(BUILTIN)
    tem_include = False
    usou_qelib = False
    declaradas_depois = []
    indices_maximos = {}

    # Primeira passada: declarações (permite detectar uso antes da declaração).
    for posicao, inst in enumerate(insts):
        t = inst.texto
        if converter_qasm3:
            m3 = re.fullmatch(r"(qubit|bit)\s*\[\s*(\d+)\s*\]\s*(\w+)\s*;?", t)
            m3b = re.fullmatch(r"(qubit|bit)\s+(\w+)\s*;?", t)
            if m3 or m3b:
                tipo = "qreg" if (m3 or m3b).group(1) == "qubit" else "creg"
                nome, n = (m3.group(3), int(m3.group(2))) if m3 else (m3b.group(2), 1)
                inst.acao, inst.nova = "substituir", f"{tipo} {nome}[{n}];"
                t = inst.nova
            elif re.fullmatch(r'include\s+"stdgates\.inc"\s*;?', t):
                inst.acao, inst.nova = "substituir", 'include "qelib1.inc";'
                t = inst.nova
        m = re.fullmatch(r"(qreg|creg)\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]\s*;?", t)
        if m:
            tipo, nome, n = m.group(1), m.group(2), int(m.group(3))
            if nome in registros["qreg"] or nome in registros["creg"]:
                inst.acao = "remover"
                problema("DUPLICATE_REGISTER", inst, f"Registrador '{nome}' declarado mais de uma vez.",
                         "Cada nome de registrador deve ser único no programa.",
                         {"tipo": "remocao", "descricao": "Remover a declaração repetida."},
                         ["Renomear o segundo registrador e atualizar as referências."], confianca="media")
                continue
            if n == 0:
                problema("EMPTY_REGISTER", inst, f"Registrador '{nome}' com tamanho 0.",
                         "Registradores precisam de pelo menos um bit.", {"tipo": "nenhuma", "descricao":
                         "Defina o tamanho pretendido."}, confianca="baixa")
            registros[tipo][nome] = {"tamanho": n, "inst": inst, "posicao": posicao}
        elif re.fullmatch(r'include\s+"([^"]+)"\s*;?', t):
            arquivo = re.fullmatch(r'include\s+"([^"]+)"\s*;?', t).group(1)
            if arquivo == "qelib1.inc":
                tem_include = True
            else:
                inst.acao = "remover"
                problema("INVALID_INCLUDE", inst, f"Include '{arquivo}' não é permitido.",
                         "Somente a biblioteca padrão qelib1.inc é aceita; arquivos externos não são lidos.",
                         {"tipo": "remocao", "descricao": "Remover o include e declarar as portas necessárias no próprio programa."},
                         confianca="media")
        gm = re.match(r"(gate|opaque)\s+([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s*([^{;]*)", t)
        if gm:
            params = [p.strip() for p in (gm.group(3) or "").split(",") if p.strip()]
            qargs = [q.strip() for q in gm.group(4).split(",") if q.strip()]
            portas[gm.group(2)] = (len(params), len(qargs))
    if converter_qasm3:
        tem_include = tem_include or any(i.nova == 'include "qelib1.inc";' for i in insts)

    # Segunda passada: operações.
    for posicao, inst in enumerate(insts):
        t = inst.nova if inst.acao == "substituir" else inst.texto
        if inst.acao == "remover" or re.match(r"(OPENQASM|include|qreg|creg|gate|opaque)\b", t):
            continue
        if getattr(inst, "sem_ponto_virgula", False) or ("\n" in t.strip()):
            linhas = [l for l in t.split("\n") if l.strip()]
            if getattr(inst, "sem_ponto_virgula", False) or len(linhas) > 1:
                corrigidas = [l.strip().rstrip(";") + ";" for l in linhas]
                problema("MISSING_SEMICOLON", inst, "Falta ';' ao final de uma instrução.",
                         "Em OpenQASM cada instrução termina com ponto e vírgula; sem ele, duas instruções se fundem.",
                         {"tipo": "substituicao", "descricao": "Separar as instruções com ';'.",
                          "novo_trecho": "\n".join(corrigidas)})
                inst.acao, inst.nova = "substituir", "\n".join(corrigidas)
                # Reanalisa cada instrução separada.
                for k, linha_nova in enumerate(corrigidas):
                    sub = Instrucao(linha_nova, inst.linha + k)
                    _analisar_operacao(sub, registros, portas, problema, politica, indices_maximos, converter_qasm3)
                    if sub.acao == "remover":
                        corrigidas[k] = None
                    elif sub.acao == "substituir":
                        corrigidas[k] = sub.nova
                    usou_qelib |= getattr(sub, "usa_qelib", False)
                inst.nova = "\n".join(c for c in corrigidas if c)
                if not inst.nova:
                    inst.acao = "remover"
                continue
        _analisar_operacao(inst, registros, portas, problema, politica, indices_maximos, converter_qasm3, posicao)
        usou_qelib |= getattr(inst, "usa_qelib", False)
        for nome in getattr(inst, "registros_usados", ()):
            for tipo in ("qreg", "creg"):
                reg = registros[tipo].get(nome)
                if reg and reg["posicao"] > posicao and reg["inst"] not in declaradas_depois:
                    declaradas_depois.append(reg["inst"])

    for decl in declaradas_depois:
        problema("USED_BEFORE_DECLARATION", decl, f"Registrador usado antes da declaração: {decl.texto}",
                 "OpenQASM 2 exige declarar registradores antes de usá-los.",
                 {"tipo": "movimentacao", "descricao": "Mover a declaração para o início do programa."})
        texto_decl = decl.nova if decl.acao == "substituir" else decl.texto
        decl.acao = "remover"
        inseridas_topo.append(texto_decl.rstrip(";") + ";")

    if usou_qelib and not tem_include:
        inseridas_topo.append('include "qelib1.inc";')
        problema("MISSING_INCLUDE", None, "Portas da biblioteca padrão usadas sem 'include \"qelib1.inc\";'.",
                 "Portas como h, cx e rz são definidas em qelib1.inc; sem o include elas não existem.",
                 {"tipo": "insercao", "descricao": "Inserir 'include \"qelib1.inc\";' após o cabeçalho."})

    if politica == "expandir_registradores":
        for (tipo, nome), maximo in indices_maximos.items():
            reg = registros[tipo][nome]
            if maximo >= reg["tamanho"]:
                novo = f"{tipo} {nome}[{maximo + 1}];"
                reg["inst"].acao, reg["inst"].nova = "substituir", novo

    corrigido = _montar(codigo, insts, inseridas_topo, cabecalho)
    return {"problemas": problemas, "codigo_corrigido": corrigido,
            "registradores": {t: {n: r["tamanho"] for n, r in regs.items()} for t, regs in registros.items()}}


def _analisar_operacao(inst, registros, portas, problema, politica, indices_maximos, qasm3, posicao=None):
    t = (inst.nova if inst.acao == "substituir" else inst.texto).strip().rstrip(";").strip()
    if not t:
        return
    inst.registros_usados = []
    # Medição OpenQASM 3: c[0] = measure q[0];
    m3 = re.fullmatch(r"(\w+(?:\[\s*\d+\s*\])?)\s*=\s*measure\s+(\w+(?:\[\s*\d+\s*\])?)", t)
    if m3:
        if not qasm3:
            problema("QASM3_SYNTAX", inst, "Medição com sintaxe de OpenQASM 3 em programa OpenQASM 2.",
                     "OpenQASM 2 usa 'measure q -> c;' em vez de 'c = measure q;'.",
                     {"tipo": "substituicao", "descricao": "Reescrever como measure ... -> ...;"},
                     categoria="conversao_de_linguagem")
        t = f"measure {m3.group(2)} -> {m3.group(1)}"
        inst.acao, inst.nova = "substituir", t + ";"
    if not qasm3 and re.fullmatch(r"(qubit|bit)\s*(\[\s*\d+\s*\])?\s*\w+", t):
        m = re.fullmatch(r"(qubit|bit)\s*(?:\[\s*(\d+)\s*\])?\s*(\w+)", t)
        tipo = "qreg" if m.group(1) == "qubit" else "creg"
        problema("QASM3_SYNTAX", inst, "Declaração com sintaxe de OpenQASM 3 em programa OpenQASM 2.",
                 "OpenQASM 2 declara registradores com qreg/creg.",
                 {"tipo": "substituicao", "descricao": f"Usar {tipo} {m.group(3)}[{m.group(2) or 1}];"},
                 categoria="conversao_de_linguagem")
        registros[tipo][m.group(3)] = {"tamanho": int(m.group(2) or 1), "inst": inst, "posicao": posicao or 0}
        inst.acao, inst.nova = "substituir", f"{tipo} {m.group(3)}[{m.group(2) or 1}];"
        return

    condicional = re.match(r"if\s*\(\s*(\w+)\s*==\s*(\d+)\s*\)\s*(.*)$", t, re.DOTALL)
    prefixo = ""
    if condicional:
        nome = condicional.group(1)
        inst.registros_usados.append(nome)
        if nome not in registros["creg"]:
            problema("UNDECLARED_REGISTER", inst, f"Registrador clássico '{nome}' não declarado na condição.",
                     "A condição de um if precisa de um creg declarado.",
                     {"tipo": "remocao", "descricao": "Remover a instrução condicional."},
                     [f"Declarar 'creg {nome}[n];' com o tamanho pretendido."], confianca="baixa")
            inst.acao = "remover"
            return
        prefixo = t[:condicional.start(3)]
        t = condicional.group(3).strip()

    if t.startswith("measure"):
        m = re.fullmatch(r"measure\s+(.+?)\s*->\s*(.+)", t)
        if not m:
            problema("INVALID_MEASURE", inst, "Medição mal formada.", "A forma correta é 'measure q[i] -> c[j];'.",
                     {"tipo": "remocao", "descricao": "Remover a medição mal formada."},
                     ["Reescrever como measure <qubit> -> <bit>;"], confianca="baixa")
            inst.acao = "remover"
            return
        q = _checar_ref(m.group(1), "qreg", inst, registros, problema, politica, indices_maximos)
        c = _checar_ref(m.group(2), "creg", inst, registros, problema, politica, indices_maximos)
        if q is None or c is None:
            return
        if q[1] is None and c[1] is None and q[2] != c[2]:
            problema("MEASURE_SIZE_MISMATCH", inst, f"measure {q[0]} -> {c[0]} com tamanhos diferentes ({q[2]} e {c[2]}).",
                     "Medir registradores inteiros exige o mesmo tamanho.",
                     {"tipo": "substituicao", "descricao": "Medir índice a índice até o menor tamanho."},
                     ["Ajustar o tamanho do creg para o do qreg."], confianca="media")
            n = min(q[2], c[2])
            inst.acao, inst.nova = "substituir", "\n".join(f"{prefixo}measure {q[0]}[{k}] -> {c[0]}[{k}];" for k in range(n))
        return

    m = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*(.*)", t, re.DOTALL)
    if not m:
        problema("UNRECOGNIZED_STATEMENT", inst, f"Instrução não reconhecida: {t[:80]}",
                 "O texto não segue a forma 'porta(parâmetros) operandos;'.",
                 {"tipo": "remocao", "descricao": "Remover a instrução."}, confianca="baixa")
        inst.acao = "remover"
        return
    nome, params_txt, operandos_txt = m.group(1), m.group(2), m.group(3).strip()
    if nome in ("barrier", "reset"):
        for ref in _dividir_args(operandos_txt):
            if _checar_ref(ref, "qreg", inst, registros, problema, politica, indices_maximos) is None:
                return
        return
    nome_final = nome
    if nome not in portas and nome not in QELIB1:
        sugestao = SINONIMOS.get(nome.lower()) or next(iter(difflib.get_close_matches(nome, list(QELIB1), 1, 0.8)), None)
        if sugestao and nome.lower() in SINONIMOS:
            nome_final = sugestao
            problema("UNKNOWN_GATE", inst, f"Porta '{nome}' não existe em OpenQASM 2.",
                     f"'{nome}' é outro nome para '{sugestao}'.",
                     {"tipo": "substituicao", "descricao": f"Usar '{sugestao}'."}, confianca="alta")
        else:
            alternativas = ["Declarar a porta com 'gate nome(params) qubits { ... }'.", "Remover a instrução."]
            if sugestao:
                alternativas.insert(0, f"Talvez a porta pretendida seja '{sugestao}'.")
            problema("UNKNOWN_GATE", inst, f"Porta '{nome}' não declarada.",
                     "A porta não está em qelib1.inc nem foi declarada no programa; sua operação não pode ser inferida.",
                     {"tipo": "remocao", "descricao": "Remover a instrução (intenção desconhecida)."}, alternativas,
                     confianca="baixa")
            inst.acao = "remover"
            return
    if nome_final in QELIB1:
        inst.usa_qelib = True
    n_params, n_qubits = portas.get(nome_final) or QELIB1[nome_final]
    params = _dividir_args(params_txt) if params_txt is not None and params_txt.strip() else []
    if len(params) != n_params:
        problema("WRONG_PARAMETER_COUNT", inst, f"'{nome_final}' espera {n_params} parâmetro(s), recebeu {len(params)}.",
                 "O valor dos ângulos ausentes ou excedentes não pode ser deduzido do código.",
                 {"tipo": "remocao", "descricao": "Remover a instrução (não há como inventar o ângulo)."},
                 [f"Informar {n_params} ângulo(s), por exemplo {nome_final}({', '.join(['pi/2'] * n_params)}) ..."]
                 if n_params else [f"Remover os parâmetros: {nome_final} ..."], confianca="baixa")
        inst.acao = "remover"
        return
    for p in params:
        ok, motivo = _expressao_valida(p)
        if not ok:
            problema("INVALID_PARAMETER", inst, f"Parâmetro inválido em '{nome_final}': {motivo}.",
                     "Ângulos em OpenQASM 2 só podem usar números, pi, operadores e funções matemáticas.",
                     {"tipo": "remocao", "descricao": "Remover a instrução (valor do ângulo desconhecido)."},
                     ["Substituir o identificador pelo valor numérico pretendido."], confianca="baixa")
            inst.acao = "remover"
            return
    refs = _dividir_args(operandos_txt)
    if len(refs) != n_qubits:
        problema("WRONG_QUBIT_COUNT", inst, f"'{nome_final}' atua em {n_qubits} qubit(s), recebeu {len(refs)}.",
                 "O número de operandos não corresponde à porta.",
                 {"tipo": "remocao", "descricao": "Remover a instrução."},
                 ["Corrigir a lista de operandos para a quantidade esperada."], confianca="baixa")
        inst.acao = "remover"
        return
    resolvidos = []
    for ref in refs:
        r = _checar_ref(ref, "qreg", inst, registros, problema, politica, indices_maximos)
        if r is None:
            return
        resolvidos.append(r)
    concretos = [(r[0], r[1]) for r in resolvidos if r[1] is not None]
    if len(concretos) != len(set(concretos)) and n_qubits > 1:
        problema("DUPLICATE_QUBIT", inst, f"'{nome_final}' usa o mesmo qubit mais de uma vez.",
                 "Portas de vários qubits exigem operandos distintos (por exemplo, controle diferente do alvo).",
                 {"tipo": "remocao", "descricao": "Remover a instrução (não é possível saber qual qubit era o pretendido)."},
                 ["Trocar um dos operandos pelo qubit pretendido."], confianca="baixa")
        inst.acao = "remover"
        return
    if nome_final != nome:
        novo = f"{prefixo}{nome_final}" + (f"({params_txt})" if params_txt is not None else "") + f" {operandos_txt};"
        inst.acao, inst.nova = "substituir", novo


def _checar_ref(ref, tipo, inst, registros, problema, politica, indices_maximos):
    m = _REF.fullmatch(ref.strip())
    if not m:
        problema("INVALID_OPERAND", inst, f"Operando inválido: '{ref.strip()}'.",
                 "Operandos são 'registrador' ou 'registrador[índice]'.",
                 {"tipo": "remocao", "descricao": "Remover a instrução."}, confianca="baixa")
        inst.acao = "remover"
        return None
    nome = m.group(1)
    indice = int(m.group(2)) if m.group(2) is not None else None
    inst.registros_usados.append(nome)
    outro = "creg" if tipo == "qreg" else "qreg"
    if nome not in registros[tipo]:
        if nome in registros[outro]:
            problema("WRONG_REGISTER_KIND", inst, f"'{nome}' é um registrador {'clássico' if tipo == 'qreg' else 'quântico'}.",
                     "Portas atuam em qreg; medições gravam em creg.", {"tipo": "remocao", "descricao": "Remover a instrução."},
                     confianca="baixa")
        else:
            problema("UNDECLARED_REGISTER", inst, f"Registrador '{nome}' não declarado.",
                     f"Todo {tipo} usado precisa ser declarado ({tipo} {nome}[n];).",
                     {"tipo": "remocao", "descricao": "Remover a instrução."},
                     [f"Declarar '{tipo} {nome}[n];' com o tamanho pretendido.",
                      *[f"Talvez seja '{c}'." for c in difflib.get_close_matches(nome, list(registros[tipo]), 1, 0.6)]],
                     confianca="baixa")
        inst.acao = "remover"
        return None
    tamanho = registros[tipo][nome]["tamanho"]
    if indice is not None and not 0 <= indice < tamanho:
        expandir = politica == "expandir_registradores" and indice >= 0
        problema("INDEX_OUT_OF_RANGE", inst,
                 f"{nome}[{indice}] não existe: o registrador {nome} tem {tamanho} posição(ões), índices de 0 a {tamanho - 1}.",
                 "O índice usado está fora do tamanho declarado do registrador.",
                 {"tipo": "expansao" if expandir else "remocao",
                  "descricao": f"Aumentar {tipo} {nome} para {indice + 1}." if expandir else
                  "Remover a instrução (não é possível saber qual índice era o pretendido)."},
                 [f"Aumentar {tipo} {nome}[{tamanho}] para [{indice + 1}] se faltou declarar posições.",
                  f"Trocar o índice por um valor entre 0 e {tamanho - 1} se foi erro de digitação."],
                 confianca="media" if expandir else "baixa")
        if expandir:
            chave = (tipo, nome)
            indices_maximos[chave] = max(indices_maximos.get(chave, -1), indice)
            return nome, indice, tamanho
        inst.acao = "remover"
        return None
    return nome, indice, tamanho


def _montar(original, insts, inseridas_topo, cabecalho):
    """Reconstrói o programa preservando linhas e comentários do que não mudou."""
    linhas = original.split("\n")
    saida = list(linhas)
    remover, substituir = set(), {}
    for inst in insts:
        if inst.acao == "manter":
            continue
        primeira = inst.linha - 1
        ultima = primeira + inst.texto.count("\n")
        for k in range(primeira, ultima + 1):
            remover.add(k)
        if inst.acao == "substituir":
            substituir[primeira] = inst.nova
    resultado = []
    for k, linha in enumerate(saida):
        if k in substituir:
            resultado.append(substituir[k])
        elif k in remover:
            continue
        else:
            resultado.append(linha)
    topo = [l for l in inseridas_topo if l.startswith("OPENQASM")] + \
           [l for l in inseridas_topo if l.startswith("include")] + \
           [l for l in inseridas_topo if not l.startswith(("OPENQASM", "include"))]
    if topo:
        # Mantém OPENQASM/include existentes antes das declarações movidas.
        indice = 0
        for k, linha in enumerate(resultado):
            if re.match(r"\s*(OPENQASM|include)\b", linha):
                indice = k + 1
        cab = [l for l in topo if l.startswith(("OPENQASM", "include"))]
        decls = [l for l in topo if not l.startswith(("OPENQASM", "include"))]
        if any(l.startswith("OPENQASM") for l in cab):
            resultado = cab + resultado
            indice += len(cab)
        else:
            resultado = resultado[:indice] + cab + resultado[indice:]
            indice += len(cab)
        resultado = resultado[:indice] + decls + resultado[indice:]
    texto = "\n".join(resultado).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", texto)
