"""API-key lifecycle (API-first §7): mint -> the key actually authenticates ->
list masks the secret -> revoke kills it on the next request.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security


@pytest.fixture
async def admin_client(tmp_path, monkeypatch):
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
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/v1/auth/login",
                                  json={"email": "admin@example.com",
                                        "password": "admin-pass-123"})
            client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            yield client


async def test_api_key_full_lifecycle(admin_client):
    # mint: full key shown exactly once, correct shape
    r = await admin_client.post("/api/v1/settings/api-keys", json={"name": "erp 集成"})
    assert r.status_code == 201, r.text
    body = r.json()
    full_key = body["api_key"]
    assert full_key.startswith("idp_ak_") and len(full_key) > 20
    assert body["prefix"] == full_key[len("idp_ak_"):][:8]

    # list: masked — prefix visible, secret absent
    r = await admin_client.get("/api/v1/settings/api-keys")
    keys = r.json()
    assert keys[0]["name"] == "erp 集成" and keys[0]["active"] is True
    assert full_key not in r.text

    # the minted key authenticates as an operator-tier integration credential
    fresh = AsyncClient(transport=admin_client._transport, base_url="http://test")
    r = await fresh.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {full_key}"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "default"

    # revoke -> next request 401
    r = await admin_client.delete(f"/api/v1/settings/api-keys/{keys[0]['id']}")
    assert r.json()["active"] is False
    r = await fresh.get("/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {full_key}"})
    assert r.status_code == 401
    await fresh.aclose()


async def test_api_key_admin_only(admin_client):
    # operator cannot manage keys
    r = await admin_client.post("/api/v1/auth/users",
                                json={"email": "op@example.com",
                                      "password": "operator-pw-1", "role": "operator"})
    assert r.status_code == 201
    r = await admin_client.post("/api/v1/auth/login",
                                json={"email": "op@example.com",
                                      "password": "operator-pw-1"})
    op_hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    fresh = AsyncClient(transport=admin_client._transport, base_url="http://test")
    assert (await fresh.get("/api/v1/settings/api-keys", headers=op_hdr)).status_code == 403
    assert (await fresh.post("/api/v1/settings/api-keys", headers=op_hdr,
                             json={"name": "x"})).status_code == 403
    await fresh.aclose()
