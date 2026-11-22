"""Credenciais do perfil e confirmação de troca de senha por e-mail."""
import hashlib
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr, StringConstraints

router = APIRouter(prefix="/auth", tags=["Conta"])
Email = Annotated[str, StringConstraints(to_lower=True, strip_whitespace=True, max_length=254,
                                       pattern=r"^[^\s@<>:,;]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")]
Password = Annotated[SecretStr, Field(min_length=8, max_length=128)]


class Address(BaseModel):
    email: Email


class Credentials(Address):
    password: Password


class Register(Credentials):
    name: str = Field(min_length=2, max_length=80)


class EditEmail(Register):
    newEmail: Email


class Verify(Address):
    challengeId: str = Field(min_length=20, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$")


class Confirm(Address):
    resetToken: str = Field(min_length=20, max_length=128)
    newPassword: Password


@contextmanager
def database():
    path = Path(os.getenv("ACCOUNT_DB", "data/accounts.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS accounts
                (email TEXT PRIMARY KEY, name TEXT NOT NULL, joined TEXT NOT NULL, password TEXT NOT NULL,
                 challenge TEXT, code TEXT, token TEXT, expires INTEGER, sent INTEGER, attempts INTEGER)""")
            db.execute("BEGIN IMMEDIATE")
            yield db
    finally:
        db.close()


def hash_password(value, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64*1024**2)
    return salt + "." + digest.hex()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def error(status, code, **extra):
    return JSONResponse({"code": code, **extra}, status_code=status)


def profile(row):
    return {"email": row["email"], "name": row["name"], "joinedAt": row["joined"]}


def authenticate(row, password):
    return row and secrets.compare_digest(row["password"], hash_password(password.get_secret_value(), row["password"].split(".")[0]))


@router.post("/register", status_code=201)
def register(data: Register):
    joined = datetime.now(timezone.utc).isoformat()
    with database() as db:
        if db.execute("SELECT 1 FROM accounts WHERE email=?", (data.email,)).fetchone():
            return error(409, "EMAIL_IN_USE")
        db.execute("INSERT INTO accounts(email,name,joined,password) VALUES (?,?,?,?)",
                   (data.email, data.name.strip(), joined, hash_password(data.password.get_secret_value())))
    return {"name": data.name.strip(), "email": data.email, "joinedAt": joined}


@router.post("/login")
def login(data: Credentials):
    with database() as db:
        row = db.execute("SELECT * FROM accounts WHERE email=?", (data.email,)).fetchone()
        if not authenticate(row, data.password):
            return error(401, "INVALID_CREDENTIALS")
        return profile(row)


@router.post("/email")
def edit_email(data: EditEmail):
    with database() as db:
        row = db.execute("SELECT * FROM accounts WHERE email=?", (data.email,)).fetchone()
        if not authenticate(row, data.password):
            return error(401, "INVALID_CREDENTIALS")
        try:
            db.execute("UPDATE accounts SET email=?,name=?,challenge=NULL,token=NULL,code=NULL WHERE email=?",
                       (data.newEmail, data.name.strip(), data.email))
        except sqlite3.IntegrityError:
            return error(409, "EMAIL_IN_USE")
    return {"ok": True}


def send_code(email, code):
    host = os.environ["SMTP_HOST"]
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM", "quantumone@localhost")
    message["To"] = email
    message["Subject"] = "QuantumOne | Código de confirmação"
    message.set_content(f"Seu código é {code}. Ele expira em 10 minutos.")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=8) as smtp:
        if os.getenv("SMTP_STARTTLS", "1") == "1":
            smtp.starttls(context=ssl.create_default_context())
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


@router.post("/password-reset/request")
def request_code(data: Address):
    now = int(time.time())
    challenge = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1000000):06d}"
    with database() as db:
        row = db.execute("SELECT sent FROM accounts WHERE email=?", (data.email,)).fetchone()
        if row and row["sent"] and now - row["sent"] < 60:
            return error(429, "RATE_LIMITED", retryAfterSeconds=60 - (now - row["sent"]))
        if row:
            db.execute("""UPDATE accounts SET challenge=?, code=?, token=NULL, expires=?, sent=?, attempts=0
                       WHERE email=?""", (challenge, hash_password(code), now+600, now, data.email))
    if row:
        try:
            send_code(data.email, code)
        except (KeyError, OSError, smtplib.SMTPException):
            with database() as db:
                db.execute("UPDATE accounts SET sent=NULL,challenge=NULL WHERE challenge=?", (challenge,))
            return error(503, "MAIL_UNAVAILABLE")
    return {"challengeId": challenge, "expiresInSeconds": 600, "resendAfterSeconds": 60,
            "localInbox": os.getenv("SMTP_HOST") == "mailpit"}


@router.post("/password-reset/verify")
def verify(data: Verify):
    now = int(time.time())
    with database() as db:
        row = db.execute("SELECT * FROM accounts WHERE email=? AND challenge=?", (data.email, data.challengeId)).fetchone()
        if not row or row["expires"] <= now:
            return error(400, "CODE_EXPIRED")
        if row["attempts"] >= 5:
            return error(429, "TOO_MANY_ATTEMPTS")
        if not secrets.compare_digest(row["code"], hash_password(data.code, row["code"].split(".")[0])):
            db.execute("UPDATE accounts SET attempts=attempts+1 WHERE email=?", (data.email,))
            return error(400, "INVALID_CODE")
        token = secrets.token_urlsafe(32)
        db.execute("UPDATE accounts SET token=?,expires=?,code=NULL,challenge=NULL WHERE email=?",
                   (digest(token), now + 600, data.email))
    return {"resetToken": token, "expiresInSeconds": 600}


@router.post("/password-reset/confirm")
def confirm(data: Confirm):
    with database() as db:
        row = db.execute("SELECT 1 FROM accounts WHERE email=? AND token=? AND expires>?",
                         (data.email, digest(data.resetToken), int(time.time()))).fetchone()
        if not row:
            return error(400, "RESET_TOKEN_EXPIRED")
        db.execute("UPDATE accounts SET password=?,token=NULL,expires=NULL WHERE email=?",
                   (hash_password(data.newPassword.get_secret_value()), data.email))
    return {"updatedAt": datetime.now(timezone.utc).isoformat()}
