"""Tenant transfer moves an identity, never its former tenant's business data."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import security, tokens
from app.db import session_factory
from app.models import ApiKey, AuditLog, AuthToken, CreditLedger, Tenant, TenantSetting, Transaction, User
from app.storage import get_storage
from tests.test_delete_task import _client, _headers, _seed_task, _seed_user

AUTH = "/api/v1/auth"
DEST = "customer"
PASSWORD = "pw12345678"


@pytest.fixture
async def env(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app):
        async with session_factory()() as s:
            s.add_all([Tenant(id=DEST, name="Customer"), Tenant(id="closed", status="closed"),
                       Tenant(id="suspended", status="suspended")])
            await s.commit()
        admin = await _seed_user("platform@example.com", "admin")
        member = await _seed_user("lead@example.com", "tenant_admin")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, admin, member


async def _transfer(c, actor, member, tenant=DEST):
    return await c.post(f"{AUTH}/users/{member['id']}/transfer", headers=_headers(actor),
                        json={"tenant_id": tenant})


async def _login(c, member, tenant):
    return await c.post(f"{AUTH}/login", json={"email": member["email"], "password": PASSWORD,
                                               "tenant_id": tenant})


async def test_transfer_changes_login_and_retains_original_task_and_key_history(env):
    c, admin, member = env
    old = await _seed_task(file_id="old-task", children=("old-child",))
    await _seed_task(file_id="new-task", tenant=DEST)
    st = get_storage()
    snapshots = {key: st.read_bytes(key) for key in old["keys"].values()}
    login = await _login(c, member, "default")
    old_session = {"Authorization": "Bearer " + login.json()["access_token"]}

    keys = {}
    async with session_factory()() as s:
        user = await s.get(User, member["id"])
        user.api_grants = {"groups": ["process", "locate"], "allowed_skill_codes": ["del_skill"]}
        txn = await s.get(Transaction, old["transaction_id"])
        txn.initiator_user_id = member["id"]
        for kind in ("agent", "application"):
            key, prefix, hashed = security.generate_api_key()
            keys[kind] = {"Authorization": f"Bearer {key}"}
            s.add(ApiKey(id=kind, tenant_id="default", key_hash=hashed, prefix=prefix,
                         key_type=kind, owner_user_id=user.id))
        reset = await tokens.issue(s, user, "reset")
        invite = await tokens.issue(s, user, "invite")
    for headers in keys.values():
        assert (await c.get("/api/v1/agent/ping", headers=headers)).status_code == 200

    r = await _transfer(c, admin, member)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == member["id"] and r.json()["tenant_id"] == DEST
    for headers in [old_session, *keys.values()]:
        assert (await c.get("/api/v1/agent/ping", headers=headers)).status_code == 401
    for purpose, token in [("reset", reset), ("activate", invite)]:
        r = await c.post(f"{AUTH}/{purpose}", json={"token": token, "password": "attacker-pass"})
        assert r.status_code == 400
    assert (await _login(c, member, "default")).status_code == 401
    fresh = await _login(c, member, DEST)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["role"] == "tenant_admin"
    new_session = {"Authorization": "Bearer " + fresh.json()["access_token"], "X-Tenant-Id": "default"}
    assert (await c.get(f"{AUTH}/me", headers=new_session)).json()["tenant_id"] == DEST
    rows = (await c.get("/api/v1/files", headers=new_session)).json()["data"]
    assert {r["file_id"] for r in rows} == {"new-task"}
    for file_id in ("old-task", "old-child"):
        assert (await c.get(f"/api/v1/files/{file_id}/download?save=1", headers=new_session)).status_code == 404
    url = "/api/v1/files/new-task/download?save=1"
    assert (await c.get(url, headers=new_session)).content == b"%PDF-orig"
    assert (await c.get("/api/v1/files/old-task/download", headers=_headers(admin))).content == b"%PDF-orig"

    # A replay cannot double-revoke credentials or copy audit records twice.
    assert (await _transfer(c, admin, member)).status_code == 404
    async with session_factory()() as s:
        user = await s.get(User, member["id"])
        assert (user.tenant_id, user.role, user.session_epoch, user.api_grants) == (DEST, "tenant_admin", 1, None)
        for kind in keys:
            key = await s.get(ApiKey, kind)
            assert key.tenant_id == "default" and key.active is False
        txn = await s.get(Transaction, old["transaction_id"])
        assert (txn.tenant_id, txn.initiator_user_id) == ("default", member["id"])
        ledger = (await s.scalars(select(CreditLedger).where(
            CreditLedger.transaction_id == txn.id))).one()
        assert (ledger.tenant_id, ledger.amount) == ("default", 3)
        events = (await s.scalars(select(AuditLog).where(AuditLog.action == "auth.user_transferred"))).all()
        assert sorted(event.tenant_id for event in events) == sorted(["default", DEST])
        for event in events:
            assert event.actor == admin["email"]
            assert event.detail["user_id"] == user.id
            assert event.detail["source_tenant_id"] == "default"
            assert event.detail["destination_tenant_id"] == DEST
    assert {key: st.read_bytes(key) for key in snapshots} == snapshots


async def test_only_platform_admin_can_transfer_only_eligible_default_members(env):
    c, platform, lead = env
    customer = await _seed_user("customer.admin@example.com", "admin", DEST)
    operator = await _seed_user("operator@example.com", "operator")
    for actor in (lead, operator, customer):
        assert (await c.get(f"{AUTH}/transfer-tenants", headers=_headers(actor))).status_code == 403
        assert (await _transfer(c, actor, operator)).status_code == 403
    dests = await c.get(f"{AUTH}/transfer-tenants", headers=_headers(platform))
    assert dests.status_code == 200 and dests.json() == [{"id": DEST, "name": "Customer"}]
    assert (await _transfer(c, platform, customer)).status_code == 404
    protected = await _seed_user("owner@example.com", "operator")
    async with session_factory()() as s:
        (await s.get(User, protected["id"])).unlimited = True
        await s.commit()
    admin = await _seed_user("legacy@example.com", "admin")
    for member in (platform, protected, admin):
        assert (await _transfer(c, platform, member)).status_code == 409
    for dest, status in [("missing", 404), ("closed", 409), ("suspended", 409), ("default", 400)]:
        r = await _transfer(c, platform, lead, dest)
        assert r.status_code == status, r.text


async def test_duplicate_email_rejected_without_partial_transfer(env):
    c, platform, lead = env
    duplicate = await _seed_user(lead["email"], "operator", DEST)
    async with session_factory()() as s:
        (await s.get(User, duplicate["id"])).active = False
        key, prefix, hashed = security.generate_api_key()
        s.add(ApiKey(id="rollback", tenant_id="default", key_hash=hashed, prefix=prefix,
                     key_type="application", owner_user_id=lead["id"]))
        await tokens.issue(s, await s.get(User, lead["id"]), "reset")
    assert (await _transfer(c, platform, lead)).status_code == 409
    assert (await c.get(f"{AUTH}/me", headers=_headers(lead))).json()["tenant_id"] == "default"
    assert (await c.get("/api/v1/agent/ping", headers={"Authorization": f"Bearer {key}"})).status_code == 200
    async with session_factory()() as s:
        assert (await s.scalar(select(AuthToken).where(AuthToken.user_id == lead["id"]))).used_at is None
        assert (await s.get(User, lead["id"])).session_epoch == 0
        assert not (await s.scalars(select(AuditLog).where(AuditLog.action == "auth.user_transferred"))).all()


async def test_two_members_competing_for_last_seat(env):
    c, platform, lead = env
    viewer = await _seed_user("viewer@example.com", "viewer")
    async with session_factory()() as s:
        s.add(TenantSetting(tenant_id=DEST, key="plan", value={"name": "starter"}))
        for i in range(4):
            s.add(User(tenant_id=DEST, email=f"seated{i}@example.com", role="viewer"))
        await s.commit()
    members = [lead, viewer]
    replies = await asyncio.gather(*(_transfer(c, platform, m) for m in members))
    assert sorted(r.status_code for r in replies) == [200, 403], [r.text for r in replies]
    for member, reply in zip(members, replies, strict=True):
        expected = DEST if reply.status_code == 200 else "default"
        assert (await _login(c, member, expected)).json()["role"] == member["role"]
    async with session_factory()() as s:
        rows = (await s.scalars(select(User).where(User.tenant_id == DEST, User.active))).all()
        assert len(rows) == 5


async def test_concurrent_duplicate_transfer_occurs_once(env):
    c, platform, lead = env
    replies = await asyncio.gather(*(_transfer(c, platform, lead) for _ in range(2)))
    assert sorted(r.status_code for r in replies) == [200, 404], [r.text for r in replies]
    async with session_factory()() as s:
        assert (await s.get(User, lead["id"])).session_epoch == 1
        events = (await s.scalars(select(AuditLog).where(AuditLog.action == "auth.user_transferred"))).all()
        assert len(events) == 2


async def test_destination_registration_requires_platform_session(env):
    c, platform, lead = env
    url = f"{AUTH}/transfer-tenants"
    body = {"id": "registered", "name": "Registered Customer"}
    customer_admin = await _seed_user("tenant-admin@example.com", "admin", DEST)
    assert (await c.post(url, json=body)).status_code == 401
    for actor in (lead, customer_admin):
        assert (await c.post(url, json=body, headers=_headers(actor))).status_code == 403
    # Calling transfer for an unknown ID must never implicitly create a tenant.
    assert (await _transfer(c, platform, lead, body["id"])).status_code == 404
    list_url = await c.get(url, headers=_headers(platform))
    assert body["id"] not in {t["id"] for t in list_url.json()}
    registered = await c.post(url, headers=_headers(platform), json=body)
    assert registered.status_code == 201, registered.text
    assert registered.json() == body
    assert body in (await c.get(url, headers=_headers(platform))).json()
    for tenant, expected in [("default", 400), ("closed", 409), ("suspended", 409), ("registered", 409)]:
        reply = await c.post(url, headers=_headers(platform), json={"id": tenant, "name": "new"})
        assert reply.status_code == expected, reply.text
    async with session_factory()() as s:
        assert (await s.get(Tenant, "closed")).status == "closed"
        assert (await s.get(Tenant, body["id"])).name == body["name"]
    assert (await _transfer(c, platform, lead, body["id"])).status_code == 200


async def test_roster_flags_reflect_transfer_eligibility(env):
    c, platform, lead = env
    async with session_factory()() as s:
        for name, kwargs in [("sso", {"auth_provider": "oidc", "external_id": "subject"}),
                             ("pending", {"email_verified": False}),
                             ("unverified", {"email_verified": False, "password_hash": "hash"}),
                             ("admin", {"role": "admin", "password_hash": "hash"}),
                             ("owner", {"unlimited": True, "password_hash": "hash"})]:
            s.add(User(tenant_id="default", email=f"{name}@example.com", **kwargs))
        await s.commit()
    roster = await c.get(f"{AUTH}/users", headers=_headers(platform))
    assert roster.status_code == 200
    assert all(row["tenant_id"] == "default" for row in roster.json())
    for member in roster.json():
        assert member["can_transfer"] is (member["id"] == lead["id"])
        if not member["can_transfer"]:
            assert (await _transfer(c, platform, member)).status_code == 409
    customer_admin = await _seed_user("customer@example.com", "admin", DEST)
    await _seed_user("customer-user@example.com", "operator", DEST)
    roster = (await c.get(f"{AUTH}/users", headers=_headers(customer_admin))).json()
    assert len(roster) == 2 and not any(row["can_transfer"] for row in roster)


async def test_auth_off_anonymous_admin_cannot_manage_tenant_assignments(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch, auth_on=False)
    async with app.router.lifespan_context(app):
        member = await _seed_user("user@example.com", "operator")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            url = f"{AUTH}/transfer-tenants"
            for response in (await c.get(url), await c.post(url, json={"id": DEST, "name": "new"}),
                             await c.post(f"{AUTH}/users/{member['id']}/transfer", json={"tenant_id": DEST})):
                assert response.status_code == 403


async def test_late_source_reset_token_cannot_change_transferred_account(env):
    c, admin, member = env
    async with session_factory()() as s:
        source_identity = await s.get(User, member['id'])
        assert (await _transfer(c, admin, member)).status_code == 200
        # A mail request that already loaded the source user can issue after transfer.
        stale = await tokens.issue(s, source_identity, "reset")
    denied = await c.post(f"{AUTH}/reset", json={"token": stale, "password": "invalid-replacement"})
    assert denied.status_code == 400
    assert (await _login(c, member, DEST)).status_code == 200
