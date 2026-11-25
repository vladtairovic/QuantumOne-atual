"""Análise unificada do QuantumOne (POST /analise).

Recebe texto, código do editor, arquivos soltos e pastas de projeto em qualquer
combinação e coordena, nesta ordem:

1. Interpretação do envio (tipo de cada arquivo, código, tracebacks, intenção).
2. Qiskit: diagnóstico de cada circuito (worker isolado, nada é executado).
3. LLM: correção do código inválido, sempre revalidada pelo Qiskit.
4. Qiskit: otimização e verificação de equivalência do código válido.
5. LLM rápida: planejamento das buscas (consultas para a base e para a internet).
6. Base local primeiro (busca híbrida com várias consultas), internet como complemento.
7. LLM: resposta final juntando pedido, resultados do Qiskit, base local e internet.

Cada etapa e cada modelo solicitado é publicado em ``rag.progresso`` para o
texto ao vivo da interface. Falhas de uma etapa não derrubam as demais.
"""
import asyncio
import json
import logging
import os
import re
import time
from uuid import uuid4

from rag import busca_chromadb, cliente_llm, conversas, pesquisa_web, progresso, resposta_local
from rag.contexto import MENSAGEM_SEM_CONTEXTO, preparar_contexto
from rag.interpretacao import interpretar
from rag.prompts import INSTRUCOES_ANALISE, INSTRUCOES_CORRECAO, INSTRUCOES_PLANEJAMENTO, INSTRUCOES_PROPOSTA

log = logging.getLogger(__name__)

PALAVRAS_DOMINIO = re.compile(
    r"qubit|qu[aâ]ntic|quantum|qiskit|qasm|circuit|porta|gate|emaranh|entangl|superposi|hadamard|grover|shor|"
    r"transpil|medi[cç][aã]o|measure|statevector|hamilton|vqe|qaoa|aer|clifford|unit[aá]ri|operador|"
    r"cnot|toffoli|ru[ií]do|noise|decoer|fidelidade|equival", re.IGNORECASE)
EXT_DOCUMENTO = (".md", ".txt", ".rst")
EXT_DADOS = (".json", ".csv", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".log")
MAX_CIRCUITOS = int(os.getenv("QONE_MAX_CIRCUITOS", "8"))
TEMPO_ANALISE = float(os.getenv("QONE_ANALISE_TIMEOUT_SECONDS", "900"))
# Tipos de correção do analisador que não perdem instruções (a LLM não melhoraria o resultado).
CORRECOES_SEM_PERDA = ("substituicao", "insercao")


# ---------------------------------------------------------------- interpretação

def _codigo_notebook(conteudo):
    try:
        celulas = json.loads(conteudo).get("cells", [])
    except (ValueError, AttributeError):
        return ""
    return "\n\n".join("".join(c.get("source", "")) for c in celulas if c.get("cell_type") == "code")


def classificar_arquivos(arquivos):
    """Classifica cada arquivo e separa circuitos candidatos e documentos de apoio."""
    classificados, circuitos, documentos = [], [], []
    for arquivo in arquivos:
        nome, conteudo = arquivo["nome"], arquivo["conteudo"]
        minusculo = nome.lower()
        tamanho = len(conteudo.encode("utf-8"))
        if not conteudo.strip():
            classificados.append({"nome": nome, "tipo": "ignorado", "tamanho": tamanho, "motivo": "vazio"})
            continue
        if minusculo.endswith(".qasm") or conteudo.lstrip().startswith("OPENQASM"):
            classificados.append({"nome": nome, "tipo": "circuito_qasm", "tamanho": tamanho, "motivo": None})
            circuitos.append({"origem": nome, "codigo": conteudo, "formato": "qasm"})
        elif minusculo.endswith(".ipynb"):
            codigo = _codigo_notebook(conteudo)
            tipo = "circuito_python" if "QuantumCircuit" in codigo else "notebook"
            classificados.append({"nome": nome, "tipo": "notebook", "tamanho": tamanho,
                                  "motivo": "células de código extraídas" if codigo else "sem células de código"})
            if tipo == "circuito_python":
                circuitos.append({"origem": nome, "codigo": codigo, "formato": "python"})
            elif codigo:
                documentos.append({"nome": nome, "texto": codigo})
        elif minusculo.endswith(".py"):
            if "QuantumCircuit" in conteudo or "qiskit" in conteudo:
                classificados.append({"nome": nome, "tipo": "circuito_python", "tamanho": tamanho, "motivo": None})
                circuitos.append({"origem": nome, "codigo": conteudo, "formato": "python"})
            else:
                classificados.append({"nome": nome, "tipo": "python", "tamanho": tamanho, "motivo": "sem Qiskit"})
                documentos.append({"nome": nome, "texto": conteudo})
        elif minusculo.endswith(EXT_DOCUMENTO):
            classificados.append({"nome": nome, "tipo": "documento", "tamanho": tamanho, "motivo": None})
            documentos.append({"nome": nome, "texto": conteudo})
        elif minusculo.endswith(EXT_DADOS):
            classificados.append({"nome": nome, "tipo": "dados", "tamanho": tamanho, "motivo": None})
            documentos.append({"nome": nome, "texto": conteudo})
        else:
            classificados.append({"nome": nome, "tipo": "ignorado", "tamanho": tamanho, "motivo": "extensão não suportada"})
    return classificados, circuitos, documentos


def circuitos_da_mensagem(mensagem):
    """Código colado na mensagem: blocos ``` com circuito, ou a mensagem inteira quando ela é código.

    Trechos inline numa pergunta (por exemplo "como usar qc.measure_all()?") não viram programa.
    """
    if not mensagem.strip():
        return []
    encontrados = []
    for lang, corpo in re.findall(r"```(\w*)[^\n]*\n(.*?)```", mensagem, re.DOTALL):
        if re.search(r"OPENQASM|qreg|qubit\s*\[|QuantumCircuit|qiskit|\.cx\(|\.h\(|\.measure", corpo):
            formato = "qasm" if lang.lower() in ("qasm", "openqasm") or re.search(r"OPENQASM|qreg|qubit\s*\[", corpo) else "python"
            encontrados.append({"origem": f"mensagem (bloco {len(encontrados) + 1})", "codigo": corpo, "formato": formato})
    if encontrados:
        return encontrados
    info = interpretar(mensagem)
    if info["tipo_entrada"] in ("qasm2", "qasm3", "python", "qasm_sem_cabecalho") or (
            "traceback" in info["componentes"] and "python" in info["componentes"]):
        from quantum.diagnostico import _segmentos
        codigo, linguagem = _segmentos(mensagem, info)
        if codigo and linguagem and len(codigo.strip().splitlines()) >= 2:
            return [{"origem": "mensagem", "codigo": codigo, "formato": linguagem}]
    return []


# ---------------------------------------------------------------- Qiskit (worker)

async def _worker(payload, rotulo):
    """Executa o worker isolado do Qiskit com o prazo configurado."""
    from quantum.api import execute_worker
    from quantum.config import get_limits
    from quantum.errors import CircuitError
    limite = get_limits().optimization_timeout_seconds
    try:
        return await asyncio.wait_for(execute_worker(payload), limite)
    except asyncio.TimeoutError as erro:
        raise CircuitError("OPTIMIZATION_TIMEOUT", f"{rotulo}: excedeu {limite:g} s.", status=504) from erro


def _bloco_codigo(texto):
    """Escolhe o maior bloco de código da resposta (o programa completo, não um trecho)."""
    blocos = re.findall(r"```(\w+)?[^\n]*\n(.*?)```", texto or "", re.DOTALL)
    if not blocos:
        return None, None
    lang, corpo = max(blocos, key=lambda b: len(b[1]))
    lang = (lang or "").lower()
    formato = "qasm" if lang in ("qasm", "openqasm") or corpo.lstrip().startswith("OPENQASM") else "python"
    return corpo.strip() + "\n", formato


def _resumo_problemas(diag, limite=12):
    return [f"linha {p['linha'] or '-'}: [{p['codigo']}] {p['mensagem']}" for p in diag.get("problemas", [])
            if p["severidade"] == "erro"][:limite]


def _erro_carregador(diag):
    erro = (diag.get("validacao") or {}).get("erro") or {}
    detalhes = erro.get("details") or {}
    partes = [erro.get("message") or "motivo não informado"]
    if detalhes.get("line"):
        partes.append(f"linha {detalhes['line']}")
    if detalhes.get("reason") and detalhes.get("reason") != erro.get("message"):
        partes.append(str(detalhes["reason"])[:200])
    return " — ".join(partes)


def _diag_valido(diag):
    return bool(diag.get("validacao", {}).get("valido")) and not any(
        p["severidade"] == "erro" for p in diag.get("problemas", []))


def _resumo_otimizacao(r):
    ao, af = r.get("analise_original") or {}, r.get("analise_final") or {}
    chaves = ("qubits", "profundidade", "total_operacoes", "operacoes_dois_qubits")
    return {"perfil": r.get("perfil"), "estrategia_aplicada": r.get("estrategia_aplicada"),
            "equivalente": r.get("equivalente"), "verificacao": {k: (r.get("verificacao") or {}).get(k)
                                                                 for k in ("metodo", "garantia", "equivalente")},
            "original": {k: r["original"].get(k) for k in chaves}, "proposta": {k: r["proposta"].get(k) for k in chaves},
            "cx_equivalentes_antes": ao.get("cx_equivalentes"), "cx_equivalentes_depois": af.get("cx_equivalentes"),
            "circuito_otimizado_qasm": r.get("circuito_otimizado_qasm", ""), "warnings": r.get("warnings", [])[:8],
            "simulacao": {k: (r.get("simulacao") or {}).get(k) for k in ("metodo", "justificativa", "memoria_statevector_bytes")},
            "compromissos": r.get("compromissos", [])}


async def analisar_circuito(candidato, perfil, rastreador, contexto_erros, diag=None, permitir_llm=True):
    origem, codigo, formato = candidato["origem"], candidato["codigo"], candidato.get("formato") or "auto"
    item = {"origem": origem, "formato": formato if formato in ("qasm", "python") else None, "valido_original": False,
            "problemas": 0, "diagnostico_resumo": [], "correcao": {"metodo": "nenhuma", "modelo": None, "codigo": None,
                                                                    "valido": False, "explicacao": None},
            "otimizacao": None, "erro": None}
    item["correcao"]["falhas"] = []
    try:
        if diag is None:
            rastreador.etapa(f"Qiskit: diagnosticando {origem}")
            diag = await _worker({"acao": "diagnosticar", "code": codigo, "format": formato, "politica": "conservadora"},
                                 f"Diagnóstico de {origem}")
        item["formato"] = diag.get("linguagem") or item["formato"]
        erros = [p for p in diag.get("problemas", []) if p["severidade"] == "erro"]
        item.update(problemas=len(erros), diagnostico_resumo=_resumo_problemas(diag))
        codigo_final = None
        if not erros and diag.get("validacao", {}).get("valido"):
            item["valido_original"] = True
            codigo_final = codigo
        else:
            deterministico = diag.get("codigo_corrigido") if diag.get("validacao", {}).get("valido") else None
            sem_perda = all((p.get("correcao") or {}).get("tipo") in CORRECOES_SEM_PERDA for p in erros)
            if deterministico:
                # O carregador só garante a construção do circuito; o programa inteiro precisa
                # passar de novo pelo diagnóstico (APIs removidas na parte de execução, por exemplo).
                rastreador.etapa(f"Qiskit: conferindo a correção automática de {origem}")
                reverificacao = await _worker({"acao": "diagnosticar", "code": deterministico, "format": item["formato"] or formato,
                                               "politica": "conservadora"}, f"Conferência de {origem}")
                completo = _diag_valido(reverificacao)
                item["correcao"] = {"metodo": "deterministica", "modelo": None, "codigo": deterministico, "valido": completo,
                                    "explicacao": ("Substituições diretas do analisador do QuantumOne (nenhuma instrução removida)."
                                                   if sem_perda else
                                                   "Correções do analisador do QuantumOne (instruções de intenção indeterminável removidas)."),
                                    "falhas": [] if completo else
                                    ["Correção automática parcial: " + f for f in _resumo_problemas(reverificacao)][:10]}
                codigo_final = deterministico  # a construção do circuito carrega: dá para otimizar
            if item["correcao"]["valido"] and sem_perda:
                # Só substituições diretas e o programa inteiro passou de novo pelo Qiskit: a LLM não é chamada.
                rastreador.registrar(f"Correção automática completa de {origem}; a LLM não foi necessária")
            elif cliente_llm.configurada() and not permitir_llm:
                item["correcao"]["falhas"] = (item["correcao"].get("falhas") or []) + [
                    "Correção pela LLM pulada: a análise chegou perto do limite de tempo."]
            elif cliente_llm.configurada():
                corrigido = await corrigir_com_llm(origem, codigo, item["formato"], diag, contexto_erros, rastreador)
                if corrigido and corrigido["valido"]:
                    item["correcao"] = corrigido
                    codigo_final = corrigido["codigo"]
                elif corrigido and not deterministico:
                    item["correcao"] = corrigido
                elif corrigido:
                    item["correcao"]["falhas"] = (item["correcao"].get("falhas", []) + [
                        "Correção da LLM recusada pelo Qiskit: " + f for f in corrigido.get("falhas", [])])[:10]
        if codigo_final:
            rastreador.etapa(f"Qiskit: otimizando e verificando {origem} (perfil {perfil})")
            resultado = await _worker({"code": codigo_final, "format": item["formato"] or "auto", "optimization_level": 3,
                                       "perfil": perfil, "prioridade": "dois_qubits", "hardware": None,
                                       "extrair_construcao": True},
                                      f"Otimização de {origem}")
            item["otimizacao"] = _resumo_otimizacao(resultado)
    except Exception as erro:  # um circuito com falha não interrompe os demais
        mensagem = getattr(erro, "message", None) or str(erro) or type(erro).__name__
        item["erro"] = mensagem[:500]
        log.warning("Circuito %s: %s", origem, mensagem[:200])
    return item


async def corrigir_com_llm(origem, codigo, formato, diag, contexto_erros, rastreador, tentativas=2):
    """Pede a correção à fila principal e revalida no Qiskit; devolve a melhor tentativa."""
    retorno = None
    problemas = _resumo_problemas(diag, 25)
    alternativas = [f"- {p['mensagem']}: " + "; ".join(p.get("alternativas", [])[:3])
                    for p in diag.get("problemas", []) if p.get("alternativas")][:15]
    for numero in range(1, tentativas + 1):
        pedido = (f"Arquivo: {origem}\nLinguagem: {formato or 'desconhecida'}\n\n<codigo_original>\n{codigo[:20000]}\n</codigo_original>\n\n"
                  f"<diagnostico_qiskit>\n" + "\n".join(problemas or ["(sem erros listados)"]) + "\n</diagnostico_qiskit>\n\n"
                  + ("<alternativas_do_analisador>\n" + "\n".join(alternativas) + "\n</alternativas_do_analisador>\n\n" if alternativas else "")
                  + (f"<versao_do_analisador>\n{diag['codigo_corrigido'][:12000]}\n</versao_do_analisador>\n\n" if diag.get("codigo_corrigido") else "")
                  + (f"<documentacao>\n{contexto_erros}\n</documentacao>\n\n" if contexto_erros else "")
                  + (f"<falha_da_tentativa_anterior>\n" + "\n".join(retorno["falhas"]) + "\n</falha_da_tentativa_anterior>\n\n"
                     if retorno and retorno.get("falhas") else "")
                  + "Devolva o programa completo corrigido em um único bloco de código e depois a explicação.")
        try:
            gerado = await asyncio.to_thread(
                cliente_llm.conversar, [{"role": "system", "content": INSTRUCOES_CORRECAO}, {"role": "user", "content": pedido}],
                None, f"Correção de {origem} (tentativa {numero})", rastreador.modelo, ao_parcial=rastreador.parcial)
        except cliente_llm.FilaEsgotada:
            rastreador.etapa(f"Nenhum modelo respondeu à correção de {origem}")
            return retorno
        novo, novo_formato = _bloco_codigo(gerado["texto"])
        explicacao = re.sub(r"```.*?```", "", gerado["texto"], flags=re.DOTALL).strip()[:2500]
        if not novo:
            retorno = {"metodo": "llm", "modelo": gerado["modelo"], "codigo": None, "valido": False,
                       "explicacao": explicacao, "falhas": ["A resposta não trouxe um bloco de código."]}
            continue
        rastreador.etapa(f"Qiskit: validando a correção de {origem} sugerida por {gerado['modelo']}")
        verificacao = await _worker({"acao": "diagnosticar", "code": novo, "format": novo_formato,
                                     "politica": "conservadora"}, f"Validação da correção de {origem}")
        valido = _diag_valido(verificacao)
        retorno = {"metodo": "llm", "modelo": gerado["modelo"], "codigo": novo, "valido": valido,
                   "explicacao": explicacao, "falhas": _resumo_problemas(verificacao) or ([] if valido else [
                       "O carregador do Qiskit não aceitou o circuito corrigido: "
                       + _erro_carregador(verificacao)])}
        if valido:
            break
    if retorno:
        retorno["falhas"] = (retorno.get("falhas") or [])[:10] if not retorno["valido"] else []
    return retorno


# ---------------------------------------------------------------- planejamento e busca

def _json(texto):
    m = re.search(r"\{.*\}", texto or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def planejar(pedido_resumido, rastreador):
    padrao = {"objetivo": pedido_resumido[:300], "consultas_base": [], "consultas_web": [], "precisa_web": True}
    if not cliente_llm.configurada() or not pedido_resumido.strip():
        return padrao, None
    try:
        gerado = cliente_llm.conversar(
            [{"role": "system", "content": INSTRUCOES_PLANEJAMENTO}, {"role": "user", "content": pedido_resumido[:6000]}],
            cliente_llm.fila_rapida(), "Planejando a busca", rastreador.modelo, temperatura=0.1, max_tokens=800)
    except cliente_llm.FilaEsgotada:
        return padrao, None
    plano = _json(gerado["texto"]) or {}
    lista = lambda chave: [str(c)[:200] for c in (plano.get(chave) or []) if str(c).strip()][:4]
    return ({"objetivo": str(plano.get("objetivo") or padrao["objetivo"])[:300], "consultas_base": lista("consultas_base"),
             "consultas_web": lista("consultas_web"), "precisa_web": bool(plano.get("precisa_web", True))},
            gerado["modelo"])


async def _buscar_contexto(consultas_base, top_k, biblioteca, mensagem, consultas_web, rastreador):
    """Base local e internet ao mesmo tempo. Nenhuma das duas usa LLM, então podem rodar
    enquanto a fila corrige os circuitos sem quebrar a regra de um modelo por vez."""
    async def base():
        inicio = time.monotonic()
        try:
            docs = await asyncio.to_thread(buscar_na_base, consultas_base, top_k, biblioteca)
            evidencia = busca_chromadb.avaliar_evidencia(mensagem or consultas_base[0], docs)
        except Exception as erro:
            log.warning("Base local indisponível: %s", type(erro).__name__)
            rastreador.registrar("Base local indisponível")
            return [], None, True
        rastreador.registrar(f"Base local: {len(docs)} trecho(s) em {time.monotonic() - inicio:.1f} s")
        return docs, evidencia, False

    async def web():
        if not consultas_web:
            return [], None
        try:
            itens, relatorio = await asyncio.to_thread(pesquisa_web.pesquisar, consultas_web)
        except Exception as erro:
            log.warning("Pesquisa na internet falhou: %s", type(erro).__name__)
            return [], {"consultas": consultas_web, "fontes": {}, "falhas": {"pesquisa": type(erro).__name__}}
        rastreador.registrar(f"Internet: {len(itens)} resultado(s) em {relatorio.get('segundos', 0)} s")
        return itens, relatorio

    return await asyncio.gather(base(), web())


def buscar_na_base(consultas, top_k, biblioteca):
    """Busca híbrida com várias consultas, unindo as seções pela melhor pontuação."""
    vistos = {}
    for consulta in consultas:
        for doc in busca_chromadb.buscar_documentos(consulta, quantidade=top_k, biblioteca=biblioteca):
            chave = (doc["metadados"]["document_id"], doc["metadados"]["secao"])
            atual = vistos.get(chave)
            if atual is None:
                vistos[chave] = {**doc, "acertos": 1}
            else:
                atual["acertos"] += 1
                atual["pontuacao"] = max(atual.get("pontuacao") or 0, doc.get("pontuacao") or 0)
    ordenados = sorted(vistos.values(), key=lambda d: (-d["acertos"], -(d.get("pontuacao") or 0)))
    return ordenados[: top_k + 2]


# ---------------------------------------------------------------- resposta final

def _fonte_local(d):
    meta = d["metadados"]
    return {"document_id": meta["document_id"], "chunk_id": d["id"], "source": meta["fonte"], "content": d["texto"],
            "origin": "local", "title": meta.get("titulo"), "retrieved_at": meta.get("coletado_em")}


def _bloco_circuitos(circuitos):
    linhas = []
    for c in circuitos:
        linhas.append(f"## {c['origem']} ({c['formato'] or 'formato desconhecido'})")
        linhas.append(f"Válido como enviado: {'sim' if c['valido_original'] else 'não'}; erros encontrados: {c['problemas']}")
        linhas += [f"- {p}" for p in c["diagnostico_resumo"][:8]]
        corr = c["correcao"]
        if corr["metodo"] != "nenhuma":
            linhas.append(f"Correção ({corr['metodo']}{', ' + corr['modelo'] if corr['modelo'] else ''}); "
                          f"validada pelo Qiskit: {'sim' if corr['valido'] else 'não'}")
            if corr.get("codigo"):
                linhas.append("```\n" + corr["codigo"][:4000] + "\n```")
        o = c.get("otimizacao")
        if o:
            v = o["verificacao"]
            linhas.append(f"Otimização (perfil {o['perfil']}, estratégia {o['estrategia_aplicada']}): portas "
                          f"{o['original']['total_operacoes']}→{o['proposta']['total_operacoes']}, profundidade "
                          f"{o['original']['profundidade']}→{o['proposta']['profundidade']}, CX equivalentes "
                          f"{o['cx_equivalentes_antes']}→{o['cx_equivalentes_depois']}; verificação {v['metodo']} "
                          f"({v['garantia']}), equivalente={v['equivalente']}.")
            if o.get("estrategia_aplicada") == "original":
                linhas.append("Circuito otimizado: igual ao corrigido (o motor não encontrou redução segura).")
            elif o.get("circuito_otimizado_qasm"):
                linhas.append("Circuito otimizado (OpenQASM 2):\n```qasm\n" + o["circuito_otimizado_qasm"][:3000] + "\n```")
        if c.get("erro"):
            linhas.append(f"Falha: {c['erro']}")
    return "\n".join(linhas)


def montar_prompt(mensagem, interpretacao, classificados, documentos, circuitos, contexto_local, contexto_web,
                  historico, plano):
    partes = [f"<pedido_do_usuario>\n{mensagem or '(sem texto; o usuário enviou código ou arquivos)'}\n</pedido_do_usuario>"]
    if historico:
        partes.append("<historico_recente>\n" + historico + "\n</historico_recente>")
    partes.append("<interpretacao>\n" + json.dumps({"intencao": interpretacao["intencao"], "tipo": interpretacao["tipo_entrada"],
                                                    "bibliotecas": interpretacao["bibliotecas"],
                                                    "objetivo_planejado": plano.get("objetivo")}, ensure_ascii=False)
                  + "\n</interpretacao>")
    if classificados:
        partes.append("<arquivos_enviados>\n" + "\n".join(f"- {a['nome']} ({a['tipo']}, {a['tamanho']} bytes)"
                                                         for a in classificados[:120]) + "\n</arquivos_enviados>")
    if documentos:
        trechos, total = [], 0
        for d in documentos:
            bloco = f"### {d['nome']}\n{d['texto'][:2500]}"
            if total + len(bloco) > 7000:
                break
            trechos.append(bloco)
            total += len(bloco)
        partes.append("<conteudo_de_arquivos>\n" + "\n\n".join(trechos) + "\n</conteudo_de_arquivos>")
    if circuitos:
        partes.append("<resultados_qiskit>\n" + _bloco_circuitos(circuitos) + "\n</resultados_qiskit>")
    partes.append("<base_local>\n" + (contexto_local or "(nenhum trecho relevante na base local)") + "\n</base_local>")
    partes.append("<internet>\n" + (contexto_web or "(pesquisa na internet não executada ou sem resultados)") + "\n</internet>")
    partes.append("Responda ao pedido seguindo as instruções.")
    return "\n\n".join(partes)


def _historico(conversation_id):
    if not conversation_id:
        return None, None, [], ""
    try:
        versao, mensagens = conversas.ler(str(conversation_id))
    except Exception:
        return None, None, [], ""
    recentes = mensagens[-4:]
    texto = "\n".join(f"{m['role']}: {m['content'][:1500]}" for m in recentes)
    return str(conversation_id), versao, mensagens, texto


def _persistir(conversation_id, versao, mensagens, mensagem_usuario, resposta, modelo):
    try:
        if conversation_id is None:
            conversation_id, versao, mensagens = conversas.criar(), 0, []
        novas = (mensagens + [{"id": str(uuid4()), "role": "user", "content": mensagem_usuario[:20000]},
                              {"role": "assistant", "content": resposta[:20000], "result": {"modelo": modelo}}])[-conversas.MAX_MENSAGENS:]
        while sum(len(m["content"]) for m in novas) > conversas.MAX_CARACTERES and len(novas) > 2:
            novas = novas[2:]
        conversas.registrar(conversation_id, versao, novas)
    except Exception as erro:  # o histórico não deve impedir a resposta
        log.warning("Histórico não gravado: %s", type(erro).__name__)
    return conversation_id


async def analisar(entrada, request_id):
    inicio = time.monotonic()
    rastreador = progresso.Rastreador(entrada.progress_id)
    avisos = []
    try:
        mensagem = entrada.mensagem.strip()
        arquivos = [{"nome": a.nome, "conteudo": a.conteudo} for a in entrada.arquivos]
        rastreador.etapa("Interpretando o envio" + (f" ({len(arquivos)} arquivo(s))" if arquivos else ""))
        classificados, candidatos, documentos = classificar_arquivos(arquivos)
        if (entrada.codigo or "").strip():
            candidatos.insert(0, {"origem": "editor", "codigo": entrada.codigo, "formato": entrada.formato})
        candidatos += circuitos_da_mensagem(mensagem)
        if len(candidatos) > MAX_CIRCUITOS:
            avisos.append(f"{len(candidatos)} circuitos encontrados; os {MAX_CIRCUITOS} primeiros foram analisados pelo Qiskit.")
            candidatos = candidatos[:MAX_CIRCUITOS]
        resumo_codigo = "\n".join(f"{c['origem']}: {c['codigo'][:600]}" for c in candidatos[:3])
        interpretacao = interpretar((mensagem + "\n" + resumo_codigo).strip() or "código")
        tracebacks = []
        if "traceback" in interpretacao["componentes"] or re.search(r"(Error|Exception):", mensagem):
            from quantum.diagnostico import analisar_traceback
            tracebacks = analisar_traceback(mensagem)

        # Conhecimento para orientar a correção (erros e migração).
        contexto_erros = ""
        if candidatos:
            try:
                consultas_erro = ["erros comuns Qiskit correção", "migração Qiskit 2 APIs removidas"]
                docs_erro = await asyncio.to_thread(buscar_na_base, consultas_erro, 3, "qiskit")
                contexto_erros, _ = preparar_contexto(docs_erro, limite=3500)
            except Exception:
                contexto_erros = ""

        # Diagnóstico de todos os circuitos (Qiskit, sem LLM): os erros orientam o planejamento da busca.
        diagnosticos = []
        for candidato in candidatos:
            rastreador.etapa(f"Qiskit: diagnosticando {candidato['origem']}")
            try:
                diagnosticos.append(await _worker({"acao": "diagnosticar", "code": candidato["codigo"],
                                                   "format": candidato.get("formato") or "auto", "politica": "conservadora"},
                                                  f"Diagnóstico de {candidato['origem']}"))
            except Exception:
                diagnosticos.append(None)  # analisar_circuito tenta de novo e registra a falha
        erros_vistos = [linha for d in diagnosticos if d for linha in _resumo_problemas(d, 2)]
        pedido_resumido = (mensagem + ("\nCódigo enviado:\n" + resumo_codigo if resumo_codigo else "")
                           + ("\nErros: " + "; ".join(erros_vistos[:6]) if erros_vistos else ""))
        sinal_dominio = bool(PALAVRAS_DOMINIO.search(mensagem)) or bool(candidatos) or bool(documentos)
        if sinal_dominio:
            plano, modelo_plano = await asyncio.to_thread(planejar, pedido_resumido, rastreador)
        else:  # sem sinal de computação quântica: não gasta LLM antes de consultar a base
            plano, modelo_plano = {"objetivo": mensagem[:300], "consultas_base": [], "consultas_web": [],
                                   "precisa_web": True}, None

        # Base local e internet não usam LLM: rodam em paralelo à correção dos circuitos.
        consultas_base = [c for c in [mensagem] + plano["consultas_base"] if c.strip()] or ["Qiskit circuito quântico"]
        web_paralela = bool(pesquisa_web.ativa() and sinal_dominio)  # internet sempre como complemento
        consultas_web = (plano["consultas_web"] or [mensagem or plano["objetivo"]]) if web_paralela else []
        texto_busca = "Buscando na base local" + (
            " e pesquisando na internet: " + "; ".join(consultas_web[:2])[:160] if consultas_web else "")
        if candidatos:
            rastreador.registrar(texto_busca + " (em paralelo aos circuitos)")
        busca = asyncio.create_task(_buscar_contexto(consultas_base, entrada.top_k, interpretacao["biblioteca_principal"],
                                                     mensagem, consultas_web, rastreador))
        try:
            circuitos = []
            limite_llm = inicio + TEMPO_ANALISE * 0.5  # depois disso, só correções automáticas
            for candidato, diag in zip(candidatos, diagnosticos):
                circuitos.append(await analisar_circuito(candidato, entrada.perfil, rastreador, contexto_erros, diag=diag,
                                                         permitir_llm=time.monotonic() < limite_llm))
            if not busca.done():
                rastreador.etapa(texto_busca)
            (documentos_base, evidencia, falha_base), (web_itens, relatorio_web) = await busca
        finally:
            if not busca.done():
                busca.cancel()
        if falha_base:
            avisos.append("A base local não respondeu; a análise seguiu sem ela.")
        contexto_local, referencias = preparar_contexto(documentos_base, limite=int(os.getenv("GROWUP_MAX_CONTEXT_CHARS", "14000"))) \
            if documentos_base else ("", [])
        usados = documentos_base[:len(referencias)]
        fontes = [_fonte_local(d) for d in usados]

        dominio = bool(PALAVRAS_DOMINIO.search(mensagem)) or bool(candidatos) or bool(
            evidencia and evidencia["suficiente"])
        if not web_paralela and pesquisa_web.ativa() and dominio:
            # sem sinal prévio de domínio, a internet só entra depois que a base confirmou o assunto
            consultas_web = plano["consultas_web"] or [mensagem or plano["objetivo"]]
            rastreador.etapa("Pesquisando na internet: " + "; ".join(consultas_web[:2])[:160])
            web_itens, relatorio_web = await asyncio.to_thread(pesquisa_web.pesquisar, consultas_web)
        if relatorio_web and relatorio_web.get("falhas"):
            avisos.append("Algumas fontes da internet não responderam: " + ", ".join(relatorio_web["falhas"]) + ".")
        fontes += pesquisa_web.como_fontes(web_itens)
        contexto_web = pesquisa_web.como_contexto(web_itens)

        conversation_id, versao, mensagens, historico = _historico(entrada.conversation_id)
        if not dominio and not candidatos and not documentos:
            resposta, status, answer_mode, info_llm = MENSAGEM_SEM_CONTEXTO + (
                " O QuantumOne responde sobre computação quântica, Qiskit e OpenQASM."), "insufficient_context", "none", None
        else:
            prompt = montar_prompt(mensagem, interpretacao, classificados, documentos, circuitos, contexto_local,
                                   contexto_web, historico, plano)
            if tracebacks:
                prompt = prompt.replace("Responda ao pedido", "Erros identificados no traceback:\n" + "\n".join(
                    f"- {t['mensagem']} -> {t['correcao']['descricao']}" for t in tracebacks) + "\n\nResponda ao pedido")
            resposta, status, answer_mode, info_llm = None, "ok", "llm", None
            if cliente_llm.configurada() and os.getenv("QONE_LLM", "auto").lower() != "local":
                try:
                    gerado = await asyncio.to_thread(
                        cliente_llm.conversar, [{"role": "system", "content": INSTRUCOES_ANALISE},
                                                {"role": "user", "content": prompt}], None, "Redigindo a resposta",
                        rastreador.modelo, ao_parcial=rastreador.parcial)
                    resposta = gerado["texto"]
                    info_llm = {"provedor": "nvidia", "modelo": gerado["modelo"], "tentativas": gerado["tentativas"]}
                    pulados = [t for t in gerado["tentativas"] if t["resultado"] != "ok"]
                    if pulados:
                        avisos.append(f"Resposta redigida por {gerado['modelo']} após {len(pulados)} modelo(s) não responderem a tempo.")
                except cliente_llm.FilaEsgotada as erro:
                    info_llm = {"provedor": "nvidia", "modelo": None, "tentativas": erro.tentativas}
                    avisos.append("Nenhum modelo da fila respondeu; a resposta abaixo foi montada localmente.")
            if resposta is None:
                answer_mode = "local"
                local = resposta_local.gerar(mensagem, usados) if mensagem and usados else None
                partes = [local["response"]] if local else []
                if circuitos:
                    partes.append("Resultados do Qiskit:\n" + _bloco_circuitos(circuitos))
                if web_itens:
                    partes.append("Resultados da internet:\n" + "\n".join(f"- {i['titulo']}: {i['url']}" for i in web_itens))
                resposta = "\n\n".join(partes) or MENSAGEM_SEM_CONTEXTO
                if not partes:
                    status, answer_mode = "insufficient_context", "none"
        rastreador.etapa("Registrando a conversa")
        conversation_id = await asyncio.to_thread(_persistir, conversation_id, versao, mensagens,
                                                  mensagem or "(código/arquivos enviados)", resposta,
                                                  (info_llm or {}).get("modelo"))
        rastreador.concluir()
        return {"request_id": request_id, "progress_id": entrada.progress_id, "conversation_id": conversation_id,
                "resposta": resposta, "status": status, "answer_mode": answer_mode, "llm": info_llm,
                "sources": fontes, "circuitos": circuitos, "arquivos": classificados, "interpretation": interpretacao,
                "evidence": evidencia, "pesquisa_web": ({**relatorio_web, "planejamento": modelo_plano} if relatorio_web
                                                        else ({"planejamento": modelo_plano} if modelo_plano else None)),
                "warnings": avisos, "etapas": rastreador.historico, "tempo_total_segundos": round(time.monotonic() - inicio, 2)}
    except Exception:
        rastreador.concluir("A análise falhou")
        raise


# ---------------------------------------------------------------- propostas de edição (CLI)

async def propor_edicao(entrada, request_id):
    """Proposta estruturada de edição para a CLI: base local como referência e a fila de LLMs
    (um modelo de cada vez). A CLI valida, mostra o diff e só grava com autorização do usuário."""
    rastreador = progresso.Rastreador(entrada.progress_id)
    try:
        rastreador.etapa("Buscando referências na base local")
        documentacao, fontes = "", []
        try:
            docs = await asyncio.to_thread(buscar_na_base, [entrada.pedido, "migração Qiskit 2 APIs removidas"], 4, "qiskit")
            documentacao, referencias = preparar_contexto(docs, limite=4000)
            fontes = [_fonte_local(d) for d in docs[:len(referencias)]]
        except Exception as erro:  # a proposta segue sem a base
            log.warning("Base local indisponível para a proposta: %s", type(erro).__name__)
        dados = {"pedido": entrada.pedido, "arquivos": entrada.arquivos, "resultado_testes": entrada.resultado_testes,
                 "documentacao": documentacao}
        gerado = await asyncio.to_thread(
            cliente_llm.conversar,
            [{"role": "system", "content": INSTRUCOES_PROPOSTA},
             {"role": "user", "content": json.dumps(dados, ensure_ascii=False)}],
            None, "Preparando a proposta", rastreador.modelo, temperatura=0.1, ao_parcial=rastreador.parcial)
        rastreador.concluir("Proposta pronta")
        return {"request_id": request_id, "response": gerado["texto"], "sources": fontes,
                "llm": {"provedor": "nvidia", "modelo": gerado["modelo"], "tentativas": gerado["tentativas"]}}
    except Exception:
        rastreador.concluir("A proposta falhou")
        raise
