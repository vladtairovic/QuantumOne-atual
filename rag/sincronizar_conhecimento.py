"""Atualização incremental do catálogo, com snapshots e limites de crescimento."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .coleta_web import Coletor, validar_url
from . import curadoria

log = logging.getLogger(__name__)
CATALOGO = Path(__file__).resolve().parents[1] / "documentos/fontes_conhecimento.json"
MAX_FONTES = 600
MAX_TRECHOS_PAGINA = 4000
MAX_TRECHOS_COLECAO = 150000


def pasta():
    return Path(os.getenv("KNOWLEDGE_PATH", "/app/data/conhecimento"))


def gravar(path, dados):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ler_status():
    try:
        return json.loads((pasta() / "status.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "not_started", "documents": []}


def _travar(arquivo):
    """Trava exclusiva não bloqueante, liberada pelo SO se o processo morrer.

    Docker/Linux usa flock; Windows (desenvolvimento local) usa msvcrt.
    """
    try:
        import fcntl
    except ImportError:
        import msvcrt
        msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)


def validar_catalogo(catalogo):
    if not isinstance(catalogo, list) or not catalogo:
        raise ValueError("Catálogo vazio ou inválido.")
    if len(catalogo) > MAX_FONTES or len({d["document_id"] for d in catalogo}) != len(catalogo):
        raise ValueError(f"Catálogo duplicado ou acima de {MAX_FONTES} fontes.")
    if len({d["url"] for d in catalogo}) != len(catalogo):
        raise ValueError("URLs duplicadas no catálogo.")
    for fonte in catalogo:
        validar_url(fonte["url"])
        if not fonte["document_id"].replace("_", "").isalnum():
            raise ValueError("Identificador de documento inválido.")


def _snapshots(destino):
    documentos = []
    for arquivo in destino.glob("*.json"):
        if arquivo.name == "status.json" or arquivo.name.endswith(".previous.json"):
            continue
        try:
            documentos.append(json.loads(arquivo.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return documentos


def curar(fonte, titulo, secoes, url_final, boilerplate=None):
    """Normaliza o documento coletado: deduplica, classifica e marca obsolescência."""
    secoes, removidos = curadoria.remover_duplicados(secoes, boilerplate)
    texto = "\n".join(b["texto"] for s in secoes for b in s["blocos"])
    obsoleto = curadoria.detectar_desatualizado(
        texto, titulo, fonte.get("biblioteca"),
        guia_migracao="migration-guides" in fonte["url"] or "migracao" in (fonte.get("temas") or []))
    return {
        "document_id": fonte["document_id"], "titulo": titulo, "fonte": url_final,
        "url_solicitada": fonte["url"], "secoes": secoes, "versao_documentacao": fonte["versao"],
        "versao_alvo_qiskit": "2.6", "coletado_em": datetime.now(timezone.utc).isoformat(),
        "blocos_duplicados_removidos": removidos,
        "desatualizado": obsoleto["desatualizado"],
        "motivo_desatualizado": "; ".join(obsoleto["motivos"])[:500],
        **curadoria.metadados_catalogo(fonte),
    }


def verificar_catalogo(saida=None, limite=None):
    """Baixa e extrai cada fonte do catálogo sem indexar (auditoria das URLs)."""
    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))
    validar_catalogo(catalogo)
    relatorio = []
    with Coletor() as coletor:
        for fonte in catalogo[:limite]:
            item = {"document_id": fonte["document_id"], "url": fonte["url"], "biblioteca": fonte.get("biblioteca")}
            try:
                titulo, secoes = coletor.coletar(fonte["url"])
                documento = curar(fonte, titulo, secoes, getattr(coletor, "ultima_url", fonte["url"]))
                item.update(status="ok", titulo=titulo[:160], secoes=len(documento["secoes"]),
                            caracteres=sum(len(b["texto"]) for s in documento["secoes"] for b in s["blocos"]),
                            desatualizado=documento["desatualizado"], url_final=documento["fonte"])
            except Exception as erro:
                item.update(status="erro", erro=f"{type(erro).__name__}: {str(erro)[:200]}")
            relatorio.append(item)
            log.info("%s %s", item["status"], fonte["url"])
            print(json.dumps(item, ensure_ascii=False), flush=True)
    resumo = {"verificado_em": datetime.now(timezone.utc).isoformat(), "total": len(relatorio),
              "ok": sum(i["status"] == "ok" for i in relatorio),
              "erros": sum(i["status"] == "erro" for i in relatorio), "fontes": relatorio}
    if saida:
        Path(saida).write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumo


def sincronizar(reindexar=False):
    from .banco import obter_colecao
    from .configuracao import MODELO_EMBEDDINGS, MODELO_LOCK, obter_modelo, limite_tokens
    from .preparar_chunks import gerar_chunks
    from .indexar_documentos import atualizar_documento
    destino = pasta()
    destino.mkdir(parents=True, exist_ok=True)
    with (destino / ".sync.lock").open("w") as trava:
        try:
            _travar(trava)
        except OSError:
            return {"status": "already_running"}
        catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))
        validar_catalogo(catalogo)
        colecao = obter_colecao()
        boilerplate = curadoria.boilerplate_entre_documentos(_snapshots(destino))
        status = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "documents": []}
        gravar(destino / "status.json", status)
        # Reindexação usa somente snapshots locais, mesmo sem internet.
        from contextlib import nullcontext
        try:
            with (nullcontext(None) if reindexar else Coletor()) as coletor:
                for fonte in catalogo:
                    doc_id = fonte["document_id"]
                    arquivo = destino / f"{doc_id}.json"
                    status["current_document"] = doc_id
                    gravar(destino / "status.json", status)
                    try:
                        anterior = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else None
                        if reindexar:
                            if anterior is None:
                                raise ValueError("Snapshot ainda não coletado.")
                            documento = anterior
                        else:
                            titulo, secoes = coletor.coletar(fonte["url"])
                            documento = curar(fonte, titulo, secoes, getattr(coletor, "ultima_url", fonte["url"]),
                                              boilerplate)
                        digest = hashlib.sha256(json.dumps(
                            [documento["secoes"], documento["fonte"], MODELO_EMBEDDINGS],
                            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                        existentes = colecao.get(where={"document_id": doc_id}, include=[])["ids"]
                        if (not reindexar and anterior and anterior.get("hash") == digest
                                and anterior.get("indexed") is True
                                and len(existentes) == anterior.get("chunks")):
                            resultado = "unchanged"
                            n = len(existentes)
                        else:
                            with MODELO_LOCK:
                                modelo = obter_modelo()
                                chunks = gerar_chunks(documento, modelo.tokenizer, limite_tokens(), MODELO_EMBEDDINGS)
                            n = len(chunks)
                            if n > MAX_TRECHOS_PAGINA or colecao.count() - len(existentes) + n > MAX_TRECHOS_COLECAO:
                                raise ValueError(f"Limite de {MAX_TRECHOS_PAGINA} trechos/página ou "
                                                 f"{MAX_TRECHOS_COLECAO} na coleção atingido.")
                            # Guarda o texto antes da escrita remota; uma falha permite repetir a operação.
                            documento.update(hash=digest, chunks=n, indexed=False)
                            if anterior and anterior.get("indexed"):
                                gravar(destino / f"{doc_id}.previous.json", anterior)
                            gravar(arquivo, documento)
                            atualizar_documento(colecao, doc_id, chunks)
                            documento["indexed"] = True
                            gravar(arquivo, documento)
                            resultado = "indexed"
                        status["documents"].append({"id": doc_id, "status": resultado, "chunks": n})
                    except Exception as erro:
                        log.warning("Coleta %s falhou: %s", doc_id, type(erro).__name__)
                        status["documents"].append({"id": doc_id, "status": "error", "error": str(erro)[:240]})
                    gravar(destino / "status.json", status)
            status["status"] = "partial" if any(d["status"] == "error" for d in status["documents"]) else "ok"
        except Exception as erro:
            status.update(status="error", error=type(erro).__name__)
        status.pop("current_document", None)
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        status["collection_chunks"] = colecao.count()
        gravar(destino / "status.json", status)
        return status


async def agendar():
    intervalo = max(3600, int(os.getenv("KNOWLEDGE_SYNC_INTERVAL_SECONDS", "604800")))
    while True:
        try:
            status = ler_status()
            ultima = status.get("finished_at")
            passou = time.time() - datetime.fromisoformat(ultima).timestamp() if ultima else intervalo
            if passou >= intervalo:
                # Processo separado permite encerrar coleta e liberar lock no desligamento da API.
                processo = await asyncio.create_subprocess_exec(
                    "python", "-m", "rag.sincronizar_conhecimento")
                try:
                    await processo.wait()
                except asyncio.CancelledError:
                    if processo.returncode is None:
                        processo.terminate()
                        await processo.wait()
                    raise
        except Exception:
            log.exception("Falha na atualização agendada.")
        await asyncio.sleep(min(intervalo, 3600))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true", help="Reconstrói usando snapshots, sem acessar a web.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--verificar-catalogo", metavar="SAIDA_JSON",
                        help="Baixa e extrai as fontes sem indexar; grava o relatório.")
    parser.add_argument("--limite", type=int, default=None)
    args = parser.parse_args()
    if args.verificar_catalogo:
        resumo = verificar_catalogo(args.verificar_catalogo, args.limite)
        print(json.dumps({k: v for k, v in resumo.items() if k != "fontes"}, ensure_ascii=True))
        raise SystemExit(0)
    resultado = ler_status() if args.status else sincronizar(args.reindex)
    print(json.dumps(resultado, ensure_ascii=True, indent=2))
    if resultado["status"] in ("error", "partial"):
        raise SystemExit(1)
