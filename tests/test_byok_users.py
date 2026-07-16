"""Tenant BYOK provider keys (§11.10) + admin user management.

Proves the key actually flows: after PUT, extraction's provider resolution
sends the TENANT key as the Bearer to the model endpoint (captured via a mock
transport), and falls back to the platform env key after DELETE.
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
    import app.extraction.byok as byok
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    byok._cache.clear()
    byok._warmed.clear()

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


async def test_byok_lifecycle_and_masking(admin_client, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "platform-key-xyz")
    r = await admin_client.get("/api/v1/settings/providers")
    assert r.status_code == 200
    providers = {p["name"]: p for p in r.json()["providers"]}
    name = next(iter(providers))
    assert providers[name]["byok_set"] is False
    assert "api_key" not in r.text and "tenant-secret" not in r.text

    r = await admin_client.put(f"/api/v1/settings/providers/{name}/key",
                               json={"api_key": "tenant-secret-key-1"})
    assert r.json()["byok_set"] is True
    r = await admin_client.get("/api/v1/settings/providers")
    assert {p["name"]: p for p in r.json()["providers"]}[name]["byok_set"] is True
    assert "tenant-secret" not in r.text          # never echoed

    # encrypted at rest
    from app.db import session_factory
    from app.models import TenantSetting
    async with session_factory()() as s:
        row = (await s.execute(
            __import__("sqlalchemy").select(TenantSetting)
            .where(TenantSetting.key == "byok"))).scalar_one()
        assert "tenant-secret-key-1" not in str(row.value)

    # unknown provider -> 404
    r = await admin_client.put("/api/v1/settings/providers/nope/key",
                               json={"api_key": "x"})
    assert r.status_code == 404

    r = await admin_client.delete(f"/api/v1/settings/providers/{name}/key")
    assert r.json()["byok_set"] is False


async def test_byok_key_reaches_the_model_call(admin_client, monkeypatch):
    """resolve_provider must prefer the tenant key over the platform env key."""
    import httpx

    from app.extraction import provider_client
    from app.tenancy import _tenant_ctx

    from app.config import load_providers
    cfg = load_providers()
    pname = cfg["active"]
    monkeypatch.setenv(cfg["providers"][pname].api_key_env, "platform-key-xyz")

    r = await admin_client.put(f"/api/v1/settings/providers/{pname}/key",
                               json={"api_key": "tenant-key-wins"})
    assert r.status_code == 200

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}}], "usage": {}})

    tok = _tenant_ctx.set("default")
    try:
        provider_client.chat_json_with_fallback(
            [{"role": "user", "content": "hi"}], [pname],
            transport=httpx.MockTransport(handler))
        assert seen["auth"] == "Bearer tenant-key-wins"

        await admin_client.delete(f"/api/v1/settings/providers/{pname}/key")
        provider_client.chat_json_with_fallback(
            [{"role": "user", "content": "hi"}], [pname],
            transport=httpx.MockTransport(handler))
        assert seen["auth"] == "Bearer platform-key-xyz"     # fallback restored
    finally:
        _tenant_ctx.reset(tok)


async def test_user_roster_and_offboarding(admin_client):
    r = await admin_client.post("/api/v1/auth/users",
                                json={"email": "leaver@example.com",
                                      "password": "initial-pw-1"})
    uid = r.json()["id"]
    r = await admin_client.get("/api/v1/auth/users")
    roster = {u["email"]: u for u in r.json()}
    assert roster["leaver@example.com"]["active"] is True

    # offboard: 人走号停
    r = await admin_client.patch(f"/api/v1/auth/users/{uid}", json={"active": False})
    assert r.json()["active"] is False
    r = await admin_client.post("/api/v1/auth/login",
                                json={"email": "leaver@example.com",
                                      "password": "initial-pw-1"})
    assert r.status_code == 401

    # admin cannot deactivate itself
    me = {u["email"]: u for u in (await admin_client.get("/api/v1/auth/users")).json()}
    admin_id = me["admin@example.com"]["id"]
    r = await admin_client.patch(f"/api/v1/auth/users/{admin_id}", json={"active": False})
    assert r.status_code == 400
