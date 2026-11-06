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


async def test_tenant_admin_assignment_and_revocation(authed_app):
    hdr = {"Authorization": f"Bearer {await _login(authed_app)}"}
    member = {"email": "lead@example.com", "password": "lead-password",
              "role": "tenant_admin"}
    created = await authed_app.post("/api/v1/auth/users", headers=hdr, json=member)
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    token = await _login(authed_app, member["email"], member["password"])
    lead = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "foreign"}
    me = (await authed_app.get("/api/v1/auth/me", headers=lead)).json()
    assert (me["role"], me["tenant_id"]) == ("tenant_admin", "default")

    # A tenant-admin session may not create privileged accounts, change its
    # own role, or enter the platform administration surface.
    assert (await authed_app.post("/api/v1/auth/users", headers=lead,
                                  json={**member, "tenant_id": "foreign"})).status_code == 403
    assert (await authed_app.patch(f"/api/v1/auth/users/{user_id}", headers=lead,
                                   json={"role": "admin"})).status_code == 403
    assert (await authed_app.get("/api/v1/settings/api-keys", headers=lead)).status_code == 403
    assert (await authed_app.get("/api/v1/billing/account", headers=lead)).status_code == 403

    for role in ("operator", "tenant_admin"):
        r = await authed_app.patch(f"/api/v1/auth/users/{user_id}", headers=hdr, json={"role": role})
        assert r.status_code == 200, r.text
        assert (await authed_app.get("/api/v1/auth/me", headers=lead)).json()["role"] == role


async def test_customer_admin_cannot_provision_across_tenants(authed_app):
    platform = {"Authorization": f"Bearer {await _login(authed_app)}"}
    admin = {"email": "customer@example.com", "password": "customer-pass-123",
             "role": "admin", "tenant_id": "customer"}
    created = await authed_app.post("/api/v1/auth/users", headers=platform, json=admin)
    assert created.status_code == 201, created.text
    customer = {"Authorization": f"Bearer {await _login(authed_app, admin['email'], admin['password'])}"}
    lead = {"email": "lead@example.com", "password": "lead-password", "role": "tenant_admin"}
    denied = await authed_app.post("/api/v1/auth/users", headers=customer,
                                   json={**lead, "tenant_id": "foreign"})
    assert denied.status_code == 403
    local = await authed_app.post("/api/v1/auth/users", headers=customer, json=lead)
    assert local.status_code == 201, local.text
    assert local.json()["tenant_id"] == "customer"
    # A valid signature alone must never authorize looking up a different tenant's row.
    mismatched = security.create_session_token(user_id=local.json()["id"], tenant_id="foreign",
                                                role="tenant_admin", email=lead["email"])
    assert (await authed_app.get("/api/v1/auth/me", headers={
        "Authorization": f"Bearer {mismatched}"})).status_code == 401


async def test_login_disambiguates_by_explicit_tenant(authed_app):
    platform = {"Authorization": f"Bearer {await _login(authed_app)}"}
    base = {"email": "shared@example.com", "role": "tenant_admin"}
    for tenant in ("tenant-a", "tenant-b"):
        created = await authed_app.post("/api/v1/auth/users", headers=platform,
                                        json={**base, "tenant_id": tenant, "password": f"{tenant}-pw"})
        assert created.status_code == 201, created.text

    creds = {"email": base["email"], "password": "tenant-b-pw"}
    ambiguous = await authed_app.post("/api/v1/auth/login", json=creds)
    mismatch = await authed_app.post("/api/v1/auth/login", json={**creds, "tenant_id": "tenant-a"})
    missing = await authed_app.post("/api/v1/auth/login", json={**creds, "tenant_id": "missing"})
    assert ambiguous.status_code == mismatch.status_code == missing.status_code == 401
    assert ambiguous.json() == mismatch.json() == missing.json()

    for tenant in ("tenant-a", "tenant-b"):
        matched = await authed_app.post("/api/v1/auth/login", headers={"X-Tenant-Id": "foreign"},
                                        json={**creds, "password": f"{tenant}-pw", "tenant_id": tenant})
        assert matched.status_code == 200, matched.text
        token = matched.json()["access_token"]
        me = await authed_app.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["tenant_id"] == tenant


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
