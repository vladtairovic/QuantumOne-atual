"""Histórico persistente com gravação atômica e controle de concorrência."""
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

MAX_MENSAGENS = 40
MAX_CARACTERES = 120_000


def conectar():
    caminho = Path(os.getenv("CONVERSAS_PATH", "data/conversas.sqlite3"))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    banco = sqlite3.connect(caminho, timeout=5)
    banco.execute(
        "CREATE TABLE IF NOT EXISTS conversas "
        "(id TEXT PRIMARY KEY, versao INTEGER NOT NULL, mensagens TEXT NOT NULL)"
    )
    return banco


def criar():
    identificador = str(uuid4())
    with conectar() as banco:
        banco.execute("INSERT INTO conversas VALUES (?, 0, '[]')", (identificador,))
    return identificador


def ler(identificador):
    with conectar() as banco:
        registro = banco.execute(
            "SELECT versao, mensagens FROM conversas WHERE id = ?", (identificador,)
        ).fetchone()
    if registro is None:
        raise HTTPException(404, "Conversa não encontrada.")
    return registro[0], json.loads(registro[1])


def registrar(identificador, versao, mensagens):
    with conectar() as banco:
        alteracao = banco.execute(
            "UPDATE conversas SET versao = versao + 1, mensagens = ? "
            "WHERE id = ? AND versao = ?",
            (json.dumps(mensagens, ensure_ascii=False), identificador, versao),
        )
        if alteracao.rowcount != 1:
            raise HTTPException(409, "A conversa mudou durante a resposta. Tente novamente.")


def preparar(mensagens, entrada):
    if len(mensagens) >= MAX_MENSAGENS or (
        sum(len(m["content"]) for m in mensagens) + len(entrada) > MAX_CARACTERES
    ):
        raise HTTPException(413, "Limite da conversa atingido. Inicie uma nova conversa.")
    # Mantém o primeiro pedido (incluindo o circuito) e até seis turnos recentes.
    historico = mensagens if len(mensagens) <= 13 else mensagens[:1] + mensagens[-12:]
    return (
        "Continue a conversa abaixo. O histórico é contexto, e não evidência documental. "
        "Fontes numeradas em respostas anteriores pertencem àquela resposta. "
        "Use apenas as fontes desta consulta nas novas citações. "
        "A aprovação permite apresentar a proposta em código, mas nenhuma ferramenta "
        "desta conversa edita arquivos ou executa código. "
        "Explique quando não houver contexto suficiente para uma referência do usuário.\n"
        + json.dumps(historico, ensure_ascii=False)
        + "\nMensagem atual do usuário:\n" + entrada
    )
