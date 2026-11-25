"""Persistência local por workspace. O desktop pode usar o mesmo contrato."""
import base64
import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4


def empacotar(dados):
    return None if dados is None else base64.b64encode(dados).decode("ascii")


def desempacotar(texto):
    return None if texto is None else base64.b64decode(texto)


class Registro:
    def __init__(self, raiz, api):
        pasta = Path(os.environ.get("QONE_STATE_DIR") or Path.home() / ".quantumone")
        pasta.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.banco = pasta / "cli.sqlite3"
        self.raiz = str(raiz)
        self.api = api
        with self.transacao() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessao (
                    id TEXT PRIMARY KEY, raiz TEXT, api TEXT, selecao TEXT, titulo TEXT,
                    conversa TEXT, assinatura TEXT, ultima TEXT);
                CREATE TABLE IF NOT EXISTS edicao (
                    id INTEGER PRIMARY KEY, raiz TEXT, plano TEXT, arquivos TEXT, estado TEXT);
                CREATE TABLE IF NOT EXISTS evento (
                    id INTEGER PRIMARY KEY, raiz TEXT, tipo TEXT, detalhe TEXT,
                    instante TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
        if os.name == "posix":
            self.banco.chmod(0o600)

    @contextmanager
    def transacao(self):
        with closing(sqlite3.connect(self.banco, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def sessoes(self):
        with self.transacao() as db:
            return db.execute("SELECT * FROM sessao WHERE raiz=? AND api=? ORDER BY rowid DESC",
                              (self.raiz, self.api)).fetchall()

    def salvar_sessao(self, sid, selecao, titulo, conversa, assinatura, ultima):
        sid = sid or str(uuid4())
        with self.transacao() as db:
            db.execute("""INSERT INTO sessao VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET selecao=excluded.selecao, conversa=excluded.conversa,
                assinatura=excluded.assinatura, ultima=excluded.ultima""",
                       (sid, self.raiz, self.api, json.dumps(selecao), titulo[:100], conversa,
                        assinatura, json.dumps(ultima, ensure_ascii=False)))
        return sid

    def esquecer(self, sid):
        with self.transacao() as db:
            db.execute("DELETE FROM sessao WHERE id=? AND raiz=? AND api=?", (sid,self.raiz,self.api))

    def evento(self, tipo, detalhe):
        with self.transacao() as db:
            db.execute("INSERT INTO evento(raiz,tipo,detalhe) VALUES(?,?,?)", (self.raiz,tipo,detalhe))

    def historico_eventos(self):
        with self.transacao() as db:
            return db.execute("SELECT instante,tipo,detalhe FROM evento WHERE raiz=? ORDER BY id DESC LIMIT 30",
                              (self.raiz,)).fetchall()
