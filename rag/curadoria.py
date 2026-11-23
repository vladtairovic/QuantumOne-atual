"""Curadoria da base: normalização, deduplicação, classificação e detecção de
conteúdo desatualizado. Usada pela sincronização administrativa e pela carga
do conhecimento interno. Não acessa a rede.
"""
import hashlib
import re
from collections import Counter

from .texto import sem_acentos

# APIs removidas ou substituídas no Qiskit 1.x/2.x, verificadas contra o Qiskit
# instalado em tests/test_conhecimento_interno.py (fonte única: quantum.migracao).
from quantum.migracao import REGRAS

_SO_CODIGO = {"identidade_i", "get_statevector_execute"}
_PADRAO_DOC = {"execute": r"from\s+qiskit\s+import\s+\(?(?:\w+\s*,\s*)*execute\b|\bqiskit\.execute\("}
PADROES_OBSOLETOS = [(_PADRAO_DOC.get(r["id"], r["padrao"]), f"{r['antigo']} -> {r['novo']} (removido: {r['removido_em']})")
                     for r in REGRAS if r["id"] not in _SO_CODIGO]
_MIGRACAO = re.compile(r"migrat|deprecat|removed|remov|release note|changelog", re.IGNORECASE)


BIBLIOTECAS_QISKIT = {"qiskit", "qiskit_aer", "qiskit_runtime", "geral", None}


def detectar_desatualizado(texto: str, titulo: str = "", biblioteca: str | None = None,
                           guia_migracao: bool = False) -> dict:
    """Marca conteúdo Qiskit que recomenda APIs removidas.

    Guias de migração são isentos (citam a API antiga de propósito), e outras
    bibliotecas também: no Amazon Braket, por exemplo, ``circuit.cnot`` é válido.
    """
    if guia_migracao or _MIGRACAO.search(titulo or "") or biblioteca not in BIBLIOTECAS_QISKIT:
        return {"desatualizado": False, "motivos": []}
    motivos = [msg for padrao, msg in PADROES_OBSOLETOS if re.search(padrao, texto)]
    return {"desatualizado": bool(motivos), "motivos": list(dict.fromkeys(motivos))}


def _normal(texto: str) -> str:
    return " ".join(sem_acentos(texto).lower().split())


def hash_bloco(texto: str) -> str:
    return hashlib.sha256(_normal(texto).encode("utf-8")).hexdigest()[:20]


def remover_duplicados(secoes: list[dict], boilerplate: set[str] | None = None) -> tuple[list[dict], int]:
    """Remove blocos repetidos dentro do documento e blocos reconhecidos como
    boilerplate (repetidos em várias páginas). Preserva código e ordem."""
    vistos, removidos, saida = set(), 0, []
    for secao in secoes:
        blocos = []
        for bloco in secao["blocos"]:
            h = hash_bloco(bloco["texto"])
            if h in vistos or (boilerplate and h in boilerplate and bloco.get("tipo") != "codigo"):
                removidos += 1
                continue
            vistos.add(h)
            blocos.append(bloco)
        if blocos:
            saida.append({**secao, "blocos": blocos})
    return saida, removidos


def boilerplate_entre_documentos(documentos: list[dict], minimo: int = 3) -> set[str]:
    """Blocos curtos que aparecem em ``minimo`` ou mais documentos (rodapés, avisos)."""
    contagem = Counter()
    for documento in documentos:
        hashes = {hash_bloco(b["texto"]) for s in documento.get("secoes", []) for b in s["blocos"]
                  if len(b["texto"]) < 400}
        contagem.update(hashes)
    return {h for h, n in contagem.items() if n >= minimo}


def metadados_catalogo(fonte: dict) -> dict:
    """Metadados escalares aceitos pelo ChromaDB (listas viram texto)."""
    temas = fonte.get("temas") or []
    return {
        "biblioteca": fonte.get("biblioteca", "geral"),
        "temas": ",".join(temas) if isinstance(temas, list) else str(temas),
        "nivel": fonte.get("nivel", "intermediario"),
        "tipo_fonte": fonte.get("tipo_fonte", "documentacao_oficial"),
        "licenca": fonte.get("licenca", "consultar termos da fonte"),
        "idioma": fonte.get("idioma", "en"),
        "origem_conhecimento": fonte.get("origem_conhecimento", "documentacao_externa"),
    }
