"""Batch F tests: challenger arbitration (design v0.2 §5.3 model channel) and
OIDC SSO with a fully mocked IdP (discovery/token/userinfo via MockTransport).
"""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import security
from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, ModelBinding, SkillPackage

UDR_SAMPLE = UDR(pages=[Page(page_no=1, width=100, height=100, blocks=[
    Block(text="INV-1 总额 1,026.50", bbox=[1, 2, 3, 4])])],
    full_markdown="INV-1 总额 1,026.50", parser="test")


def _pkg(challenger: str | None) -> SkillPackage:
    return SkillPackage(skill_code="arb", name="仲裁",
                        fields=[FieldSpec(name="invoice_no", instruction="号码"),
                                FieldSpec(name="total", instruction="总额")],
                        model_binding=ModelBinding(extractor="qwen", challenger=challenger))


def test_challenger_disagreement_forces_review(monkeypatch):
    """Primary and challenger disagree on total -> confidence capped, review."""
    import app.extraction.pipeline as pipe

    def fake_chat(messages, chain, transport=None):
        if chain == ["gpt"]:          # challenger pass
            return ({"invoice_no": "INV-1", "total": "999"},
                    {"prompt_tokens": 5, "completion_tokens": 2}, "gpt")
        return ({"invoice_no": "INV-1", "total": "1,026.50"},
                {"prompt_tokens": 10, "completion_tokens": 5}, "qwen")

    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_chat)
    result, usage, needs_review = pipe.extract(UDR_SAMPLE, _pkg("gpt"))

    assert usage["challenger_used"] == "gpt"
    # agreement: kept its earned confidence, marker present
    assert result["invoice_no"]["$challenger"] == {"value": "INV-1", "agree": True}
    assert result["invoice_no"]["$confidence"] == 3
    # disagreement: capped to 1, second opinion recorded, file needs review
    assert result["total"]["$challenger"] == {"value": "999", "agree": False}
    assert result["total"]["$confidence"] == 1
    assert needs_review is True


def test_challenger_agreement_and_normalization(monkeypatch):
    """'1,026.50' vs '1026.50' must count as agreement (separator-insensitive)."""
    import app.extraction.pipeline as pipe

    def fake_chat(messages, chain, transport=None):
        val = "1,026.50" if chain != ["gpt"] else "1026.50"
        return ({"invoice_no": "INV-1", "total": val},
                {"prompt_tokens": 5, "completion_tokens": 2},
                "gpt" if chain == ["gpt"] else "qwen")

    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_chat)
    result, usage, needs_review = pipe.extract(UDR_SAMPLE, _pkg("gpt"))
    assert result["total"]["$challenger"]["agree"] is True
    assert needs_review is False


def test_challenger_failure_never_breaks_the_file(monkeypatch):
    import app.extraction.pipeline as pipe

    def fake_chat(messages, chain, transport=None):
        if chain == ["gpt"]:
            raise RuntimeError("challenger vendor down")
        return ({"invoice_no": "INV-1", "total": "1,026.50"},
                {"prompt_tokens": 10, "completion_tokens": 5}, "qwen")

    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_chat)
    result, usage, needs_review = pipe.extract(UDR_SAMPLE, _pkg("gpt"))
    assert "challenger_error" in usage
    assert "$challenger" not in result["total"]      # arbitration silently absent
    assert result["invoice_no"]["$value"] == "INV-1"


# —— OIDC ——

ISSUER = "https://idp.example.com"


def _idp_transport(userinfo: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "userinfo_endpoint": f"{ISSUER}/userinfo"})
        if path.endswith("/token"):
            assert b"grant_type=authorization_code" in request.content
            return httpx.Response(200, json={"access_token": "at-123"})
        if path.endswith("/userinfo"):
            assert request.headers["Authorization"] == "Bearer at-123"
            return httpx.Response(200, json=userinfo)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
async def sso_client(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    from app.auth import oidc
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    oidc._discovery.clear()
    oidc._transport = _idp_transport({"sub": "idp-sub-42",
                                      "email": "sso.user@example.com",
                                      "name": "SSO User"})

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/v1/auth/login",
                                  json={"email": "admin@example.com",
                                        "password": "admin-pass-123"})
            admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await client.put("/api/v1/settings/oidc", headers=admin, json={
                "enabled": True, "issuer": ISSUER, "client_id": "idp-client",
                "client_secret": "idp-secret"})
            assert r.status_code == 200 and r.json()["has_secret"]
            yield client
    oidc._transport = None


async def test_oidc_full_flow_jit_and_rebind(sso_client):
    from app.auth import oidc

    # enabled probe is public
    r = await sso_client.get("/api/v1/auth/oidc/enabled")
    assert r.json() == {"enabled": True}

    # login redirect carries client_id + a valid state
    r = await sso_client.get("/api/v1/auth/oidc/login")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(f"{ISSUER}/authorize") and "client_id=idp-client" in loc
    state = httpx.URL(loc).params["state"]
    assert oidc.check_state(state)

    # callback: code exchange -> JIT user -> platform JWT handed to frontend
    r = await sso_client.get("/api/v1/auth/oidc/callback",
                             params={"code": "authcode-1", "state": state})
    assert r.status_code == 302
    target = r.headers["location"]
    assert "#/oidc?token=" in target
    token = target.split("token=")[1]
    r = await sso_client.get("/api/v1/auth/me",
                             headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "sso.user@example.com"

    # JIT user recorded with the IdP hard link
    from sqlalchemy import select
    from app.db import session_factory
    from app.models import User
    async with session_factory()() as s:
        u = (await s.execute(select(User).where(
            User.email == "sso.user@example.com"))).scalar_one()
        assert (u.auth_provider, u.external_id, u.role) == ("oidc", "idp-sub-42", "operator")

    # tampered/expired state bounces to login with an error, no session
    r = await sso_client.get("/api/v1/auth/oidc/callback",
                             params={"code": "authcode-2", "state": "garbage"})
    assert r.status_code == 302 and "error=sso_state" in r.headers["location"]


async def test_oidc_email_anchor_binds_existing_account(sso_client, monkeypatch):
    """Pre-existing local account with the IdP email gets bound (no duplicate)."""
    from app.auth import oidc
    oidc._transport = _idp_transport({"sub": "idp-sub-77",
                                      "email": "admin@example.com", "name": "Admin"})
    r = await sso_client.get("/api/v1/auth/oidc/login")
    state = httpx.URL(r.headers["location"]).params["state"]
    r = await sso_client.get("/api/v1/auth/oidc/callback",
                             params={"code": "authcode-3", "state": state})
    assert "#/oidc?token=" in r.headers["location"]

    from sqlalchemy import select
    from app.db import session_factory
    from app.models import User
    async with session_factory()() as s:
        rows = (await s.execute(select(User).where(
            User.email == "admin@example.com"))).scalars().all()
        assert len(rows) == 1                        # bound, not duplicated
        assert rows[0].external_id == "idp-sub-77"
        assert rows[0].role == "admin"               # keeps its role
