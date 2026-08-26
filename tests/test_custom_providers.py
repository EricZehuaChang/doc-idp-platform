"""Console-registered model channels (2026-08-26).

The security posture is the point of most of these: the key is stored
encrypted, never echoed, admin-only, and a custom name can never shadow a
built-in channel (otherwise "which model produced this" has two answers).
"""
import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, SkillPackage

UDR_S = UDR(pages=[Page(page_no=1, blocks=[Block(text="发票号 INV-7", bbox=[1, 1, 2, 2])])],
            full_markdown="发票号 INV-7", parser="t")
PKG = SkillPackage(skill_code="cp", name="cp", fields=[FieldSpec(name="invoice_no")])


async def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    from app.extraction import custom_providers
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    custom_providers._cache.clear()
    custom_providers._warmed.clear()
    from app.main import create_app
    return create_app()


async def test_register_list_and_delete_never_echoes_the_key(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            secret = "sk-super-secret-value"
            r = await c.put("/api/v1/settings/custom-providers/my-vlm",
                            json={"base_url": "https://vendor.example/v1/",
                                  "model": "vlm-max", "api_key": secret,
                                  "vision": True})
            assert r.status_code == 200, r.text
            assert r.json() == {"name": "my-vlm", "model": "vlm-max",
                                "base_url": "https://vendor.example/v1",
                                "vision": True, "has_key": True}

            body = (await c.get("/api/v1/settings/custom-providers")).json()
            assert body["providers"] == [{
                "name": "my-vlm", "model": "vlm-max",
                "base_url": "https://vendor.example/v1", "vision": True,
                "has_key": True, "extra_body": {}}]
            # the secret must not appear anywhere in any response
            assert secret not in r.text
            assert secret not in (await c.get("/api/v1/settings/custom-providers")).text
            assert secret not in (await c.get("/api/v1/skills/model-options")).text

            # ...nor in the audit trail
            from app.db import session_factory
            from app.models import AuditLog
            from sqlalchemy import select
            sf = session_factory()
            async with sf() as s:
                logs = (await s.execute(select(AuditLog))).scalars().all()
            assert any(x.action == "settings.custom_provider_set" for x in logs)
            assert all(secret not in str(x.detail) for x in logs)

            # editing without resupplying the key keeps the stored one
            r = await c.put("/api/v1/settings/custom-providers/my-vlm",
                            json={"base_url": "https://vendor.example/v1",
                                  "model": "vlm-pro"})
            assert r.json()["has_key"] is True and r.json()["model"] == "vlm-pro"

            assert (await c.delete("/api/v1/settings/custom-providers/my-vlm")
                    ).status_code == 200
            assert (await c.get("/api/v1/settings/custom-providers")).json()["providers"] == []
            assert (await c.delete("/api/v1/settings/custom-providers/my-vlm")
                    ).status_code == 404


async def test_rejects_shadowing_builtins_and_bad_input(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            # a custom channel named after a yaml one would make provider
            # resolution ambiguous — refuse it
            r = await c.put("/api/v1/settings/custom-providers/qwen",
                            json={"base_url": "https://x.example/v1", "api_key": "k"})
            assert r.status_code == 400 and "内置" in r.json()["detail"]

            r = await c.put("/api/v1/settings/custom-providers/no-scheme",
                            json={"base_url": "vendor.example/v1", "api_key": "k"})
            assert r.status_code == 400

            # first registration without a key would store a dead channel
            r = await c.put("/api/v1/settings/custom-providers/keyless",
                            json={"base_url": "https://x.example/v1"})
            assert r.status_code == 400
            assert (await c.get("/api/v1/settings/custom-providers")).json()["providers"] == []


async def test_registered_channel_is_pickable_and_actually_gets_called(tmp_path, monkeypatch):
    """End of the loop: register -> appears in the picker -> a dry-run against
    it really hits that base_url with that model and that key."""
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            await c.put("/api/v1/settings/custom-providers/trial",
                        json={"base_url": "https://trial.example/v1",
                              "model": "trial-omni-1", "api_key": "sk-trial"})
            opts = (await c.get("/api/v1/skills/model-options")).json()
            row = next(p for p in opts["providers"] if p["name"] == "trial")
            assert row == {"name": "trial", "model": "trial-omni-1",
                           "active": False, "custom": True, "vision": False}

            seen: list[httpx.Request] = []

            def _reply(req: httpx.Request) -> httpx.Response:
                seen.append(req)
                return httpx.Response(200, json={"choices": [{"message": {
                    "content": '{"invoice_no": "INV-7"}'}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

            with respx.mock:
                respx.post("https://trial.example/v1/chat/completions").mock(
                    side_effect=_reply)
                from app.extraction import custom_providers
                from app.db import session_factory
                sf = session_factory()
                async with sf() as s:
                    await custom_providers.warm(s, "default", force=True)
                import asyncio

                from app.skillengine import studio
                runs = await asyncio.to_thread(studio.dry_run, UDR_S, PKG, ["trial"])

    assert runs[0]["ok"] is True, runs
    assert runs[0]["result"]["invoice_no"]["$value"] == "INV-7"
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer sk-trial"
    import json as _json
    assert _json.loads(seen[0].content)["model"] == "trial-omni-1"


async def test_vision_channel_receives_image_parts(tmp_path, monkeypatch):
    """A channel flagged as vision-capable gets page rasters beside the text;
    a text-only channel must never receive image parts."""
    import json as _json

    from app.skillengine.compiler import compile_messages

    text_only = compile_messages(PKG, UDR_S)
    assert isinstance(text_only[-1]["content"], str)

    with_img = compile_messages(PKG, UDR_S, ["data:image/jpeg;base64,AAAA"])
    parts = with_img[-1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "text" and "发票号 INV-7" in parts[0]["text"]
    assert parts[1] == {"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
    # the payload stays a plain JSON-serialisable body
    assert _json.dumps(with_img)


@pytest.fixture
async def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    from app.auth import security
    from app.extraction import custom_providers
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    custom_providers._cache.clear()
    custom_providers._warmed.clear()
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


async def test_channel_management_is_admin_only(admin_client):
    """Registering a model endpoint means handing the platform a credential —
    an operator must not be able to add, read, delete or test one."""
    r = await admin_client.post("/api/v1/auth/users",
                                json={"email": "op@example.com",
                                      "password": "operator-pw-1", "role": "operator"})
    assert r.status_code == 201
    r = await admin_client.post("/api/v1/auth/login",
                                json={"email": "op@example.com",
                                      "password": "operator-pw-1"})
    op = {"Authorization": f"Bearer {r.json()['access_token']}"}
    fresh = AsyncClient(transport=admin_client._transport, base_url="http://test")
    assert (await fresh.get("/api/v1/settings/custom-providers",
                            headers=op)).status_code == 403
    assert (await fresh.put("/api/v1/settings/custom-providers/x", headers=op,
                            json={"base_url": "https://x.example/v1",
                                  "api_key": "k"})).status_code == 403
    assert (await fresh.delete("/api/v1/settings/custom-providers/x",
                               headers=op)).status_code == 403
    assert (await fresh.post("/api/v1/settings/custom-providers/x/test",
                             headers=op)).status_code == 403
    await fresh.aclose()
