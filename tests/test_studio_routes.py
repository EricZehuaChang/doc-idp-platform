"""9.15 WP3 studio endpoints: samples are operator+ only, tenant-scoped,
suffix/size limited; generate-fields returns a draft (never persisted)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security


@pytest.fixture
async def auth_env(tmp_path, monkeypatch):
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
            for email, role in [("op@example.com", "operator"),
                                ("view@example.com", "viewer")]:
                await client.post("/api/v1/auth/users",
                                  json={"email": email, "password": "pw-123456",
                                        "role": role})
            yield client


async def _hdr(client, email):
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": "pw-123456"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_sample_upload_limits(auth_env):
    # bad suffix
    r = await auth_env.post("/api/v1/studio/samples",
                            files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sample_type_rejected"
    r = await auth_env.post("/api/v1/studio/samples",
                            files={"file": ("s.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 201, r.text
    assert r.json()["file_name"] == "s.png"


async def test_sample_role_and_tenant_gates(auth_env):
    # viewer cannot upload/list (§3.7: studio is operator+)
    v = await _hdr(auth_env, "view@example.com")
    r = await auth_env.post("/api/v1/studio/samples", headers=v,
                            files={"file": ("s.png", b"\x89PNG", "image/png")})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "role_required"
    assert (await auth_env.get("/api/v1/studio/samples", headers=v)).status_code == 403

    # operator uploads; list is tenant-scoped
    o = await _hdr(auth_env, "op@example.com")
    r = await auth_env.post("/api/v1/studio/samples", headers=o,
                            files={"file": ("s.png", b"\x89PNG", "image/png")})
    assert r.status_code == 201
    sid = r.json()["id"]

    # a row from another tenant must not be visible or deletable (existence hidden)
    from app.db import session_factory
    from app.models import StudioSample
    async with session_factory()() as s:
        s.add(StudioSample(tenant_id="other_tenant", uploader_id="x",
                           file_name="forigen.png", storage_key="studio/other/x/a.png"))
        await s.commit()
    r = await auth_env.get("/api/v1/studio/samples", headers=o)
    ids = [x["id"] for x in r.json()["samples"]]
    assert sid in ids and len(ids) == 1
    r = await auth_env.get("/api/v1/studio/samples/foreign-id/file", headers=o)
    assert r.status_code == 404

    # operator deletes their own sample -> row gone
    r = await auth_env.delete(f"/api/v1/studio/samples/{sid}", headers=o)
    assert r.status_code == 204
    assert (await auth_env.get("/api/v1/studio/samples", headers=o)).json()["samples"] == []


async def test_generate_fields_requires_input_and_returns_draft(auth_env, monkeypatch):
    o = await _hdr(auth_env, "op@example.com")
    r = await auth_env.post("/api/v1/studio/generate-fields", headers=o, json={})
    assert r.status_code == 400

    # viewer denied
    v = await _hdr(auth_env, "view@example.com")
    r = await auth_env.post("/api/v1/studio/generate-fields", headers=v,
                            json={"description": "发票号、日期"})
    assert r.status_code == 403

    # operator + mocked generation: draft comes back, nothing persisted
    from app.skillengine import studio as studio_core
    from app.skillengine.schema import FieldSpec

    def fake_generate(sample_text, description, provider=None, transport=None):
        assert "发票号" in description
        return {"fields": [FieldSpec(name="invoice_no", type="string",
                                     instruction="发票号码")],
                "examples": {"invoice_no": "INV-001"}, "doc_type": "",
                "usage": {}, "provider_used": "fake"}
    monkeypatch.setattr(studio_core, "generate_fields", fake_generate)
    r = await auth_env.post("/api/v1/studio/generate-fields", headers=o,
                            json={"description": "发票号、金额、日期"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fields"][0]["name"] == "invoice_no"
    assert body["examples"]["invoice_no"] == "INV-001"

    # generation failure path: no fields -> 422
    monkeypatch.setattr(studio_core, "generate_fields",
                        lambda *a, **k: {"fields": [], "examples": {},
                                         "doc_type": "", "usage": {},
                                         "provider_used": "fake"})
    r = await auth_env.post("/api/v1/studio/generate-fields", headers=o,
                            json={"description": "什么都没有"})
    assert r.status_code == 422
