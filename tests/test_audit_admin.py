"""2026-09-09 需求: 管理员操作日志 + 管理员重置用户密码.

Covers the two new surfaces end to end:
- GET /audit/logs (admin-only, tenant-scoped, q/action filters, pagination)
- POST /auth/users/{id}/reset-password (admin, same-tenant, self-ban, SSO/pending
  409) and its session semantics: the reset bumps session_epoch so a token
  minted before the reset stops resolving (tenancy._resolve_bearer compares
  the token's ep claim with the row).
"""
from httpx import ASGITransport, AsyncClient


def _pkg(**kw) -> dict:
    from app.skillengine.schema import FieldSpec, SkillPackage
    return SkillPackage(skill_code="auditpkg", name="审计测试",
                        fields=[FieldSpec(name="invoice_no")], **kw).model_dump()


async def _client(tmp_path, monkeypatch, *, auth_on=False):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    if auth_on:
        monkeypatch.setenv("IDP_AUTH_MODE", "on")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    return create_app()


async def _seed_user(email: str, role: str, tenant: str = "default",
                     *, password: str | None = "pw12345678",
                     provider: str = "local", epoch: int = 0, active: bool = True):
    from app.auth import security
    from app.db import session_factory
    from app.models import User
    sf = session_factory()
    async with sf() as s:
        u = User(tenant_id=tenant, email=email, role=role,
                 password_hash=security.hash_password(password) if password else None,
                 auth_provider=provider, active=active, session_epoch=epoch)
        s.add(u)
        await s.commit()
        return {"id": u.id, "tenant_id": u.tenant_id, "role": u.role, "email": u.email,
                "epoch": epoch}


def _token(u: dict) -> str:
    from app.auth import security
    return security.create_session_token(
        user_id=u["id"], tenant_id=u["tenant_id"], role=u["role"],
        email=u["email"], session_epoch=u["epoch"])


def _headers(u: dict) -> dict:
    return {"Authorization": f"Bearer {_token(u)}"}


# —— password reset: admin flow and session semantics ——

async def test_admin_reset_password_flow_and_session_kill(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch, auth_on=True)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            target = await _seed_user("worker@example.com", "operator")

            # a token minted BEFORE the reset must die with it
            old = _headers(target)
            assert (await c.get("/api/v1/auth/me", headers=old)).status_code == 200

            r = await c.post(f"/api/v1/auth/users/{target['id']}/reset-password",
                             headers=_headers(admin),
                             json={"password": "brand-new-pass1"})
            assert r.status_code == 200, r.text
            assert r.json() == {"id": target["id"], "email": "worker@example.com",
                                "status": "password_reset",
                                "must_change_password": True}

            # old session dead; login with the new password works and forces change
            assert (await c.get("/api/v1/auth/me", headers=old)).status_code == 401
            r = await c.post("/api/v1/auth/login",
                             json={"email": "worker@example.com",
                                   "password": "brand-new-pass1"})
            assert r.status_code == 200 and r.json()["must_change_password"] is True
            fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
            assert (await c.get("/api/v1/auth/me", headers=fresh)).status_code == 200
            # the old password is really gone
            assert (await c.post("/api/v1/auth/login",
                                 json={"email": "worker@example.com",
                                       "password": "pw12345678"})).status_code == 401

            # the operation itself is on the log, with the admin as actor
            rows = (await c.get("/api/v1/audit/logs", headers=_headers(admin))).json()
            hit = [x for x in rows["data"]
                   if x["action"] == "auth.password_reset_admin"]
            assert len(hit) == 1
            assert hit[0]["actor"] == "boss@example.com"
            assert hit[0]["detail"] == {"email": "worker@example.com"}


async def test_admin_reset_password_guards(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch, auth_on=True)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            operator = await _seed_user("op@example.com", "operator")
            other_admin = await _seed_user("other@example.com", "admin",
                                           tenant="otherco")
            sso = await _seed_user("sso@example.com", "operator",
                                   provider="oidc", password=None)
            pending = await _seed_user("inv@example.com", "operator", password=None)

            body = {"password": "newpass1234"}
            # role gate
            r = await c.post(f"/api/v1/auth/users/{operator['id']}/reset-password",
                             headers=_headers(operator), json=body)
            assert r.status_code == 403
            # cross-tenant target is invisible
            r = await c.post(f"/api/v1/auth/users/{other_admin['id']}/reset-password",
                             headers=_headers(admin), json=body)
            assert r.status_code == 404
            # SSO-only / not-yet-activated accounts have no local password
            r = await c.post(f"/api/v1/auth/users/{sso['id']}/reset-password",
                             headers=_headers(admin), json=body)
            assert r.status_code == 409
            r = await c.post(f"/api/v1/auth/users/{pending['id']}/reset-password",
                             headers=_headers(admin), json=body)
            assert r.status_code == 409
            # admin may not reset self (mirrors 不能停用自己的账号)
            r = await c.post(f"/api/v1/auth/users/{admin['id']}/reset-password",
                             headers=_headers(admin), json=body)
            assert r.status_code == 400
            # weak password
            r = await c.post(f"/api/v1/auth/users/{operator['id']}/reset-password",
                             headers=_headers(admin), json={"password": "short"})
            assert r.status_code == 422
            # nothing above touched the target
            assert (await c.post("/api/v1/auth/login",
                                 json={"email": "op@example.com",
                                       "password": "pw12345678"})).status_code == 200


# —— audit log reader: admin-only, tenant-scoped, filters, pagination ——

async def test_audit_logs_admin_view_filters_and_pagination(tmp_path, monkeypatch):
    app = await _client(tmp_path, monkeypatch, auth_on=True)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            admin = await _seed_user("boss@example.com", "admin")
            operator = await _seed_user("op@example.com", "operator")
            admin_h = _headers(admin)

            # no rows yet (fresh db) — empty is fine, not an error
            assert (await c.get("/api/v1/audit/logs",
                                headers=admin_h)).json()["total"] == 0

            # operator is not allowed to read the log
            assert (await c.get("/api/v1/audit/logs",
                                headers=_headers(operator))).status_code == 403

            # produce some rows through real operations
            r = await c.post("/api/v1/skills", headers=admin_h,
                             json={"package": _pkg(), "changelog": ""})
            assert r.status_code == 201
            await c.post("/api/v1/skills/auditpkg/versions/1/publish",
                         headers=admin_h)
            await c.patch("/api/v1/skills/auditpkg/state", headers=admin_h,
                          json={"action": "disable"})
            # login rows from the operator's own session
            await c.post("/api/v1/auth/login", json={"email": "op@example.com",
                                                     "password": "pw12345678"})
            # an audit row in ANOTHER tenant must stay invisible
            from app.db import session_factory
            from app.models import AuditLog
            sf = session_factory()
            async with sf() as s:
                s.add(AuditLog(tenant_id="otherco", actor="x@other.test",
                               action="skills.created", detail={"skill_code": "x"}))
                await s.commit()

            rows = (await c.get("/api/v1/audit/logs",
                                headers=admin_h)).json()
            assert rows["total"] >= 4
            actions = [x["action"] for x in rows["data"]]
            assert "skills.created" in actions and "skills.published" in actions
            assert "skills.state_changed" in actions and "auth.login" in actions
            assert all(x["actor"] != "x@other.test" for x in rows["data"])
            # newest first
            ts = [x["created_at"] for x in rows["data"]]
            assert ts == sorted(ts, reverse=True)

            # action-prefix filter narrows to the family
            sk = (await c.get("/api/v1/audit/logs", params={"action": "skills"},
                              headers=admin_h)).json()
            assert all(x["action"].startswith("skills.") for x in sk["data"])
            # keyword search on actor
            q = (await c.get("/api/v1/audit/logs",
                             params={"q": "op@example.com"},
                             headers=admin_h)).json()
            assert q["total"] >= 1 and all("op@example.com" in x["actor"]
                                           or "op@example.com" in x["action"]
                                           for x in q["data"])
            # LIKE metacharacters in user input match literally
            q2 = (await c.get("/api/v1/audit/logs", params={"q": "a%b"},
                              headers=admin_h)).json()
            assert q2["total"] == 0

            # pagination: page=1&limit=2 pages the same set with a stable total
            p1 = (await c.get("/api/v1/audit/logs", params={"limit": 2, "page": 1},
                              headers=admin_h)).json()
            p2 = (await c.get("/api/v1/audit/logs", params={"limit": 2, "page": 2},
                              headers=admin_h)).json()
            assert len(p1["data"]) == 2 and p1["total"] == p2["total"]
            ids1 = [x["id"] for x in p1["data"]]
            assert not any(x["id"] in ids1 for x in p2["data"])
            # cap the page size
            assert (await c.get("/api/v1/audit/logs",
                                params={"limit": 500},
                                headers=admin_h)).status_code == 422


async def test_audit_logs_skills_lifecycle_written(tmp_path, monkeypatch):
    """Skills lifecycle ops now leave audit rows (they previously wrote none)."""
    app = await _client(tmp_path, monkeypatch)  # auth off: synthetic admin
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            r = await c.post("/api/v1/skills", json={"package": _pkg(),
                                                     "changelog": "v1"})
            assert r.status_code == 201
            await c.patch("/api/v1/skills/auditpkg/state", json={"action": "disable"})
            await c.patch("/api/v1/skills/auditpkg/state", json={"action": "enable"})
            await c.post("/api/v1/skills/auditpkg/versions/1/publish")
            await c.post("/api/v1/skills/auditpkg/versions", json={
                "package": _pkg(), "changelog": "branch v2"})
            await c.delete("/api/v1/skills/auditpkg/versions/2")
            await c.delete("/api/v1/skills/auditpkg")
            await c.post("/api/v1/skills/auditpkg/restore")

            rows = (await c.get("/api/v1/audit/logs")).json()["data"]
        from collections import Counter
        got = Counter(x["action"] for x in rows)
        want = {"skills.created": 1, "skills.state_changed": 2,
                "skills.published": 1, "skills.version_deleted": 1,
                "skills.deleted": 1, "skills.restored": 1}
        assert got == want, got
        assert all(x["actor"] == "anonymous" for x in rows)
