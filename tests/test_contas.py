from fastapi import FastAPI
from fastapi.testclient import TestClient

import contas


def test_cadastro_perfil_e_recuperacao(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNT_DB", str(tmp_path / "accounts.db"))
    deliveries = []
    monkeypatch.setattr(contas, "send_code", lambda email, code: deliveries.append((email, code)))
    app = FastAPI()
    app.include_router(contas.router)
    client = TestClient(app)

    def post(route, **body):
        return client.post("/auth/" + route, json=body)

    account = {"email": "ana@example.invalid", "password": "senha-de-teste"}
    assert post("register", name="Ana", **account).status_code == 201
    assert post("register", name="Ana", **account).status_code == 409
    assert post("login", **account).json()["name"] == "Ana"
    new_email = "outro@example.invalid"
    assert post("email", email=account["email"], password="incorreta", newEmail=new_email, name="Ana").status_code == 401
    assert post("email", **account, newEmail=new_email, name="Ana Silva").is_success
    assert post("login", **account).status_code == 401
    account["email"] = new_email
    assert post("login", **account).json()["name"] == "Ana Silva"
    challenge = post("password-reset/request", email=new_email).json()["challengeId"]
    assert post("password-reset/request", email=new_email).status_code == 429
    recipient, code = deliveries.pop()
    assert recipient == new_email
    payload = {"email": new_email, "challengeId": challenge}
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    assert post("password-reset/verify", **payload, code=wrong).json()["code"] == "INVALID_CODE"
    token = post("password-reset/verify", **payload, code=code).json()["resetToken"]
    assert post("password-reset/verify", **payload, code=code).status_code == 400
    reset = {"email": new_email, "resetToken": token, "newPassword": "senha-nova-segura"}
    assert post("password-reset/confirm", **reset).is_success
    assert post("password-reset/confirm", **reset).status_code == 400
    assert post("login", **account).status_code == 401
    assert post("login", email=new_email, password=reset["newPassword"]).is_success
