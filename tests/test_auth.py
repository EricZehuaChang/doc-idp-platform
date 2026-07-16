"""Auth tests (§11.9): JWT login, protected /api surface, role gate, API-key
channel, and the trust rule "tenant comes from the credential, not the header".
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security


def test_password_hash_roundtrip():
    h = security.hash_password("s3cret-pw")
    assert security.verify_password("s3cret-pw", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("s3cret-pw", "garbage-not-a-hash")


@pytest.fixture
async def authed_app(tmp_path, monkeypatch):
    """App with auth_mode=on and a bootstrap admin; fresh sqlite per test."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None          # process-global; isolate per test app

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            yield client


async def _login(client, email="admin@example.com", password="admin-pass-123") -> str:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def test_api_requires_credential(authed_app):
    r = await authed_app.get("/api/v1/skills")
    assert r.status_code == 401
    r = await authed_app.get("/api/v1/skills", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    # liveness probes stay public
    assert (await authed_app.get("/healthz")).status_code == 200


async def test_login_and_me(authed_app):
    token = await _login(authed_app)
    r = await authed_app.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"email": "admin@example.com", "role": "admin", "tenant_id": "default"}
    # wrong password -> 401, same shape as unknown user (no account oracle)
    r = await authed_app.post("/api/v1/auth/login",
                              json={"email": "admin@example.com", "password": "nope"})
    r2 = await authed_app.post("/api/v1/auth/login",
                               json={"email": "ghost@example.com", "password": "nope"})
    assert r.status_code == r2.status_code == 401
    assert r.json() == r2.json()


async def test_tenant_comes_from_token_not_header(authed_app):
    token = await _login(authed_app)
    r = await authed_app.get("/api/v1/auth/me",
                             headers={"Authorization": f"Bearer {token}",
                                      "X-Tenant-Id": "someone-elses-tenant"})
    assert r.json()["tenant_id"] == "default"   # header must be ignored when auth is on


async def test_role_gate_on_user_creation(authed_app):
    admin_token = await _login(authed_app)
    hdr = {"Authorization": f"Bearer {admin_token}"}
    r = await authed_app.post("/api/v1/auth/users", headers=hdr,
                              json={"email": "op@example.com", "password": "operator-pw-1",
                                    "role": "operator"})
    assert r.status_code == 201, r.text
    # duplicate in same tenant -> 409
    r = await authed_app.post("/api/v1/auth/users", headers=hdr,
                              json={"email": "op@example.com", "password": "operator-pw-1"})
    assert r.status_code == 409

    op_token = await _login(authed_app, "op@example.com", "operator-pw-1")
    r = await authed_app.post("/api/v1/auth/users",
                              headers={"Authorization": f"Bearer {op_token}"},
                              json={"email": "x@example.com", "password": "whatever-pw-1"})
    assert r.status_code == 403                 # operator cannot mint users


async def test_disabled_user_rejected_next_request(authed_app):
    admin_token = await _login(authed_app)
    hdr = {"Authorization": f"Bearer {admin_token}"}
    r = await authed_app.post("/api/v1/auth/users", headers=hdr,
                              json={"email": "leaver@example.com", "password": "leaver-pw-99"})
    assert r.status_code == 201
    token = await _login(authed_app, "leaver@example.com", "leaver-pw-99")

    from sqlalchemy import update
    from app.db import session_factory
    from app.models import User
    async with session_factory()() as s:
        await s.execute(update(User).where(User.email == "leaver@example.com").values(active=False))
        await s.commit()

    r = await authed_app.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401                 # valid JWT, dead account


async def test_api_key_channel(authed_app):
    from app.db import session_factory
    from app.models import ApiKey
    full_key = "idp_ak_test_0123456789"
    async with session_factory()() as s:
        s.add(ApiKey(tenant_id="acme", key_hash=security.hash_api_key(full_key),
                     name="ci-bot"))
        await s.commit()

    hdr = {"Authorization": f"Bearer {full_key}"}
    r = await authed_app.get("/api/v1/auth/me", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme"          # tenant follows the key
    assert body["role"] == "operator"
    # API keys must not manage users
    r = await authed_app.post("/api/v1/auth/users", headers=hdr,
                              json={"email": "y@example.com", "password": "whatever-pw-1"})
    assert r.status_code == 403
