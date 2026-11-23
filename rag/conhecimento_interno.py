"""Camada de conhecimento técnico próprio do QuantumOne.

Os textos ficam em ``documentos/conhecimento_interno/*.md`` (autoria do
projeto, não documentação oficial de terceiros). Blocos de código marcados
como ``validar`` são executados pela suíte de testes contra o Qiskit
instalado antes de qualquer publicação; a carga na base nunca executa código.

Cada documento entra na coleção com ``origem_conhecimento=qone_interno`` e
fonte ``qone://conhecimento/<id>``, para que respostas e interface possam
distinguir conhecimento interno de documentação externa.
"""
import hashlib
import json
import re
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1] / "documentos" / "conhecimento_interno"
VERSAO = "QuantumOne conhecimento interno v1 (validado no Qiskit 2.6)"
_CERCA = re.compile(r"^```(\w+)?(?:\s+(validar))?\s*$")


def _front_matter(texto):
    if not texto.startswith("---"):
        raise ValueError("Documento interno sem cabeçalho.")
    _, cabecalho, corpo = texto.split("---", 2)
    meta = {}
    for linha in cabecalho.strip().splitlines():
        chave, _, valor = linha.partition(":")
        meta[chave.strip()] = valor.strip()
    for obrigatorio in ("id", "titulo", "biblioteca", "temas", "nivel"):
        if not meta.get(obrigatorio):
            raise ValueError(f"Cabeçalho sem {obrigatorio}.")
    return meta, corpo


def interpretar_markdown(texto):
    """Converte o Markdown em seções e blocos no formato dos snapshots."""
    meta, corpo = _front_matter(texto)
    secoes, atual, paragrafo, codigo = [], None, [], None

    def fechar_paragrafo():
        if paragrafo and atual is not None:
            atual["blocos"].append({"tipo": "paragrafo", "texto": " ".join(paragrafo).strip()})
        paragrafo.clear()

    for linha in corpo.splitlines():
        if codigo is not None:
            if linha.strip() == "```":
                atual["blocos"].append({"tipo": "codigo", "texto": "\n".join(codigo["linhas"]),
                                        "linguagem": codigo["lang"], "validar": codigo["validar"]})
                codigo = None
            else:
                codigo["linhas"].append(linha)
            continue
        cerca = _CERCA.match(linha.strip())
        if cerca:
            fechar_paragrafo()
            codigo = {"lang": cerca.group(1) or "texto", "validar": bool(cerca.group(2)), "linhas": []}
            continue
        if linha.startswith("## "):
            fechar_paragrafo()
            atual = {"titulo": linha[3:].strip(), "blocos": []}
            secoes.append(atual)
            continue
        if atual is None:
            continue
        item = linha.strip()
        if not item:
            fechar_paragrafo()
        elif item.startswith(("- ", "* ")) or re.match(r"^\d+\. ", item) or item.startswith("|"):
            fechar_paragrafo()
            if not re.match(r"^\|[-|\s]+\|$", item):
                atual["blocos"].append({"tipo": "paragrafo", "texto": item.lstrip("-* ").strip()})
        else:
            paragrafo.append(item)
    if codigo is not None:
        raise ValueError("Bloco de código não fechado.")
    fechar_paragrafo()
    secoes = [s for s in secoes if s["blocos"]]
    if not secoes:
        raise ValueError("Documento interno sem seções.")
    documento = {
        "document_id": meta["id"], "titulo": meta["titulo"], "fonte": f"qone://conhecimento/{meta['id']}",
        "versao_documentacao": VERSAO, "versao_alvo_qiskit": "2.6", "coletado_em": "conteúdo versionado no repositório",
        "secoes": secoes, "biblioteca": meta["biblioteca"], "temas": meta["temas"], "nivel": meta["nivel"],
        "tipo_fonte": "conhecimento_interno", "origem_conhecimento": "qone_interno",
        "licenca": "conteúdo original do projeto QuantumOne", "idioma": "pt",
    }
    documento["hash_conteudo"] = hashlib.sha256(json.dumps(
        [documento["secoes"], documento["titulo"]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return documento


def carregar(pasta=PASTA):
    documentos = [interpretar_markdown(p.read_text(encoding="utf-8")) for p in sorted(pasta.glob("*.md"))]
    ids = [d["document_id"] for d in documentos]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs duplicados no conhecimento interno.")
    return documentos


def blocos_validaveis(documentos):
    for documento in documentos:
        for secao in documento["secoes"]:
            for bloco in secao["blocos"]:
                if bloco["tipo"] == "codigo" and bloco.get("validar"):
                    yield documento["document_id"], secao["titulo"], bloco["linguagem"], bloco["texto"]


def indexar(colecao, documentos=None):
    """Indexa documentos novos ou alterados; mantém os que já estão atualizados."""
    from .configuracao import MODELO_EMBEDDINGS, MODELO_LOCK, obter_modelo, limite_tokens
    from .indexar_documentos import atualizar_documento
    from .preparar_chunks import gerar_chunks
    documentos = carregar() if documentos is None else documentos
    resumo = {"indexados": 0, "inalterados": 0}
    for documento in documentos:
        existentes = colecao.get(where={"document_id": documento["document_id"]}, include=["metadatas"])
        hashes = {m.get("hash_conteudo") for m in existentes["metadatas"]}
        if existentes["ids"] and hashes == {documento["hash_conteudo"]}:
            resumo["inalterados"] += 1
            continue
        secoes = [{"titulo": s["titulo"], "blocos": [{"tipo": b["tipo"], "texto": b["texto"]} for b in s["blocos"]]}
                  for s in documento["secoes"]]
        with MODELO_LOCK:
            modelo = obter_modelo()
            chunks = gerar_chunks({**documento, "secoes": secoes}, modelo.tokenizer, limite_tokens(), MODELO_EMBEDDINGS)
        for chunk in chunks:
            chunk["metadados"]["hash_conteudo"] = documento["hash_conteudo"]
        atualizar_documento(colecao, documento["document_id"], chunks)
        resumo["indexados"] += 1
    return resumo
