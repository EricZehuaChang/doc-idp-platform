"""Account lifecycle tests (design v0.2 §11.8): invite -> activate -> login,
forgot -> reset, resend rate limit, SMTP config write-only-no-echo, and the
no-SMTP degradation to admin activation mode. Mail delivery is stubbed at the
network edge (mailer._deliver) — no real SMTP anywhere.
"""
import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security
from app.notify import mailer

SENT: list[dict] = []


@pytest.fixture
async def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    SENT.clear()

    async def fake_deliver(cfg, to, subject, body):
        SENT.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(mailer, "_deliver", fake_deliver)

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            yield client


async def _admin(client) -> dict:
    r = await client.post("/api/v1/auth/login",
                          json={"email": "admin@example.com", "password": "admin-pass-123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _configure_smtp(client, hdr) -> None:
    r = await client.put("/api/v1/settings/smtp", headers=hdr, json={
        "host": "smtp.example.com", "port": 465, "security": "ssl",
        "username": "noreply@example.com", "password": "smtp-secret",
        "from_addr": "noreply@example.com", "from_name": "IDP"})
    assert r.status_code == 200, r.text


def _token_from(body: str) -> str:
    return re.search(r"token=([\w\-]+)", body).group(1)


async def test_invite_without_smtp_degrades(app_client):
    hdr = await _admin(app_client)
    r = await app_client.post("/api/v1/auth/invite", headers=hdr,
                              json={"email": "new@example.com"})
    assert r.status_code == 409                     # admin activation mode instead
    assert "SMTP" in r.json()["detail"]


async def test_invite_activate_login_flow(app_client):
    hdr = await _admin(app_client)
    await _configure_smtp(app_client, hdr)
    r = await app_client.post("/api/v1/auth/invite", headers=hdr,
                              json={"email": "op@example.com", "role": "operator"})
    assert r.status_code == 201, r.text
    assert SENT and SENT[-1]["to"] == "op@example.com"
    token = _token_from(SENT[-1]["body"])

    # dormant account cannot log in before activation
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "op@example.com", "password": "whatever-pw"})
    assert r.status_code == 401

    r = await app_client.post("/api/v1/auth/activate",
                              json={"token": token, "password": "fresh-pw-123"})
    assert r.status_code == 200, r.text
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "op@example.com", "password": "fresh-pw-123"})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is False

    # token is single-use
    r = await app_client.post("/api/v1/auth/activate",
                              json={"token": token, "password": "again-pw-123"})
    assert r.status_code == 400


async def test_invite_resend_rate_limited(app_client):
    hdr = await _admin(app_client)
    await _configure_smtp(app_client, hdr)
    r = await app_client.post("/api/v1/auth/invite", headers=hdr,
                              json={"email": "slow@example.com"})
    assert r.status_code == 201
    r = await app_client.post("/api/v1/auth/invite", headers=hdr,
                              json={"email": "slow@example.com"})
    assert r.status_code == 429                     # 60s cooldown (§11.8)


async def test_forgot_reset_flow_and_no_account_oracle(app_client):
    hdr = await _admin(app_client)
    await _configure_smtp(app_client, hdr)
    # unknown email answers 200 with the same body as a real one
    r1 = await app_client.post("/api/v1/auth/forgot", json={"email": "ghost@example.com"})
    r2 = await app_client.post("/api/v1/auth/forgot", json={"email": "admin@example.com"})
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert SENT[-1]["to"] == "admin@example.com"    # only the real one got mail
    token = _token_from(SENT[-1]["body"])

    r = await app_client.post("/api/v1/auth/reset",
                              json={"token": token, "password": "new-admin-pw-1"})
    assert r.status_code == 200
    # old password dead, new one lives
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "admin@example.com", "password": "admin-pass-123"})
    assert r.status_code == 401
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "admin@example.com", "password": "new-admin-pw-1"})
    assert r.status_code == 200


async def test_change_password_and_first_login_force_flag(app_client):
    hdr = await _admin(app_client)
    # admin-created account carries must_change_password
    r = await app_client.post("/api/v1/auth/users", headers=hdr,
                              json={"email": "fresh@example.com", "password": "initial-pw-1"})
    assert r.status_code == 201
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "fresh@example.com", "password": "initial-pw-1"})
    assert r.json()["must_change_password"] is True
    user_hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await app_client.post("/api/v1/auth/change-password", headers=user_hdr,
                              json={"old_password": "wrong", "new_password": "self-set-pw-1"})
    assert r.status_code == 401
    r = await app_client.post("/api/v1/auth/change-password", headers=user_hdr,
                              json={"old_password": "initial-pw-1",
                                    "new_password": "self-set-pw-1"})
    assert r.status_code == 200
    r = await app_client.post("/api/v1/auth/login",
                              json={"email": "fresh@example.com", "password": "self-set-pw-1"})
    assert r.json()["must_change_password"] is False


async def test_smtp_config_never_echoes_password(app_client):
    hdr = await _admin(app_client)
    await _configure_smtp(app_client, hdr)
    r = await app_client.get("/api/v1/settings/smtp", headers=hdr)
    body = r.json()
    assert body["configured"] is True and body["has_password"] is True
    assert "password" not in body and "password_enc" not in body
    assert "smtp-secret" not in r.text
    # PUT without password keeps the stored secret usable
    r = await app_client.put("/api/v1/settings/smtp", headers=hdr, json={
        "host": "smtp2.example.com", "port": 587, "security": "starttls",
        "username": "noreply@example.com", "from_addr": "noreply@example.com"})
    assert r.json()["has_password"] is True
    # encrypted at rest: raw value absent from the DB row
    from app.db import session_factory
    from app.models import PlatformSetting
    async with session_factory()() as s:
        row = await s.get(PlatformSetting, "smtp")
        assert "smtp-secret" not in str(row.value)
        assert mailer.decrypt_password(row.value["password_enc"]) == "smtp-secret"


async def test_smtp_test_endpoint(app_client):
    hdr = await _admin(app_client)
    await _configure_smtp(app_client, hdr)
    r = await app_client.post("/api/v1/settings/smtp/test", headers=hdr,
                              json={"to": "check@example.com"})
    assert r.status_code == 200
    assert SENT[-1]["to"] == "check@example.com"
