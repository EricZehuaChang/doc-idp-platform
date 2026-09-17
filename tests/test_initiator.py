"""9.15 WP2: initiator snapshot (R21), agent-key permission chain (§3.7),
and submit idempotency. Auth-on flows use real logins; the parser and LLM are
faked so submissions complete without external calls.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import security
from tests.test_core import PKG, UDR_SAMPLE


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
    from app.parsers import router as prouter
    monkeypatch.setattr(prouter, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.extraction.pipeline as pipe

    def fake_extract_llm(messages, chain, transport=None):
        return ({"invoice_no": "INV-2026-001", "total": "1,026.50",
                 "expense_type": {"value": "差旅", "reasoning": "出租车费"}},
                {"prompt_tokens": 10, "completion_tokens": 5}, "fake")
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_extract_llm)
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


@pytest.fixture
async def seeded_skill(auth_env):
    r = await auth_env.post("/api/v1/skills", json={"package": PKG.model_dump()})
    assert r.status_code == 201, r.text
    r = await auth_env.post("/api/v1/skills/invoice_test/versions/1/publish")
    assert r.status_code == 200
    return "invoice_test"


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _mk_user(client: AsyncClient, email: str, role: str) -> None:
    r = await client.post("/api/v1/auth/users",
                          json={"email": email, "password": "pw-123456", "role": role})
    assert r.status_code == 201, r.text


async def _submit(client: AsyncClient, skill: str, blob: bytes = b"%PDF-fake",
                  headers: dict | None = None, idem: str | None = None):
    h = dict(headers or {})
    if idem:
        h["Idempotency-Key"] = idem
    return await client.post("/api/v1/process",
                             files={"files": ("inv.pdf", blob, "application/pdf")},
                             data={"skill_code": skill}, headers=h)


# —— R21: initiator snapshot ————————————————————————————————————————————

async def test_initiator_snapshot_user_jwt_and_legacy_rows(auth_env, seeded_skill):
    # JWT submission: label = user email, type = user, user_id pinned
    r = await _submit(auth_env, seeded_skill)
    assert r.status_code == 202, r.text
    tid = r.json()["transaction_id"]
    r = await auth_env.get("/api/v1/files")
    row = next(x for x in r.json()["data"] if x["transaction_id"] == tid)
    assert row["initiator_type"] == "user"
    assert row["initiator_label"] == "admin@example.com"

    # legacy rows (predating the columns): unknown / 历史任务, never fabricated
    from app.db import session_factory
    from app.models import FileRecord, Transaction
    async with session_factory()() as s:
        legacy_txn = Transaction(tenant_id="default", skill_code=seeded_skill,
                                 skill_version=1, status="completed")
        s.add(legacy_txn)
        await s.flush()
        s.add(FileRecord(tenant_id="default", transaction_id=legacy_txn.id,
                         file_name="legacy.pdf", storage_path="files/x/legacy.pdf",
                         status="completed"))
        await s.commit()
    r = await auth_env.get("/api/v1/files")
    legacy = next(x for x in r.json()["data"]
                  if x["initiator_type"] == "unknown")
    # no label for legacy rows: the UI renders "—"; the migrated-DB backfill
    # (initiator_label=历史任务) is verified in tests/test_migrations.py
    assert legacy["initiator_label"] is None


async def test_initiator_anonymous_in_auth_off(tmp_path, monkeypatch, seeded_skill=None):
    # lite/dev (auth off): submissions still record an honest anonymous actor
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t2.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "off")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.parsers import router as prouter
    monkeypatch.setattr(prouter, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback",
                        lambda m, c, transport=None: ({"invoice_no": "INV-2026-001"},
                                                      {}, "fake"))
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            assert r.status_code == 201, r.text
            await client.post("/api/v1/skills/invoice_test/versions/1/publish")
            r = await _submit(client, "invoice_test")
            assert r.status_code == 202, r.text
            r = await client.get("/api/v1/files")
            row = r.json()["data"][0]
            assert row["initiator_type"] == "anonymous"
            assert row["initiator_label"] == "anonymous"


# —— §3.7: agent key chain ——————————————————————————————————————————————

async def _agent_key(client: AsyncClient, hdr: dict, name: str,
                     skills: list[str] | None) -> str:
    r = await client.post("/api/v1/me/api-keys", headers=hdr,
                          json={"name": name, "allowed_skill_codes": skills})
    assert r.status_code == 201, r.text
    return r.json()["key"]


async def test_agent_key_whitelist_and_ping(auth_env, seeded_skill):
    await _mk_user(auth_env, "op@example.com", "operator")
    op_hdr = await _login(auth_env, "op@example.com", "pw-123456")
    key = await _agent_key(auth_env, op_hdr, "前台扫描仪", ["invoice_test"])
    ah = {"Authorization": f"Bearer {key}"}
    fresh = AsyncClient(transport=auth_env._transport, base_url="http://test")

    # ping: identity + tenant + scope-aware count
    r = await fresh.get("/api/v1/agent/ping", headers=ah)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_name"] == "前台扫描仪"
    assert body["tenant_name"]                    # display name, not the slug
    assert body["skill_count"] == 1 and body["server_version"]

    # skills: only published + active + in scope
    r = await fresh.get("/api/v1/agent/skills", headers=ah)
    assert r.status_code == 200
    skills = r.json()["skills"]
    assert [s["skill_code"] for s in skills] == ["invoice_test"]
    assert skills[0]["published_version"] == 1
    assert skills[0]["processing_mode"] == "balanced"

    # whitelist default-deny: agent key cannot browse files or skills admin
    assert (await fresh.get("/api/v1/files", headers=ah)).status_code == 403
    assert (await fresh.get("/api/v1/skills", headers=ah)).status_code == 403
    r = await fresh.get("/api/v1/files", headers=ah)
    assert r.json()["detail"]["code"] == "key_scope_denied"

    # last_used_at recorded (throttled writes; first request stamps it)
    mine = (await auth_env.get("/api/v1/me/api-keys", headers=op_hdr)).json()["keys"]
    assert mine and mine[0]["last_used_at"] is not None
    await fresh.aclose()


async def test_agent_key_dies_with_owner(auth_env, seeded_skill):
    await _mk_user(auth_env, "op2@example.com", "operator")
    op_hdr = await _login(auth_env, "op2@example.com", "pw-123456")
    key = await _agent_key(auth_env, op_hdr, "车间终端", None)
    ah = {"Authorization": f"Bearer {key}"}
    fresh = AsyncClient(transport=auth_env._transport, base_url="http://test")
    assert (await fresh.get("/api/v1/agent/ping", headers=ah)).status_code == 200

    # admin suspends the owner -> the key is dead on the very next request
    users = (await auth_env.get("/api/v1/auth/users")).json()
    uid = next(u["id"] for u in users if u["email"] == "op2@example.com")
    r = await auth_env.patch(f"/api/v1/auth/users/{uid}", json={"active": False})
    assert r.status_code == 200
    r = await fresh.get("/api/v1/agent/ping", headers=ah)
    assert r.status_code == 401
    await fresh.aclose()


async def test_agent_key_submit_scope_and_viewer_role(auth_env, seeded_skill):
    # viewer's key can be minted but can never submit (§3.7 row 提交任务)
    await _mk_user(auth_env, "view@example.com", "viewer")
    v_hdr = await _login(auth_env, "view@example.com", "pw-123456")
    vkey = await _agent_key(auth_env, v_hdr, "只读终端", None)
    vh = {"Authorization": f"Bearer {vkey}"}
    fresh = AsyncClient(transport=auth_env._transport, base_url="http://test")
    r = await _submit(fresh, seeded_skill, headers=vh)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "role_required"
    # viewer key still reads the skill menu (skills:read scope)
    assert (await fresh.get("/api/v1/agent/skills", headers=vh)).status_code == 200

    # operator key scoped to another skill -> 403 skill_not_allowed_for_key
    await _mk_user(auth_env, "op3@example.com", "operator")
    o_hdr = await _login(auth_env, "op3@example.com", "pw-123456")
    okey = await _agent_key(auth_env, o_hdr, "财务终端", ["other_skill"])
    oh = {"Authorization": f"Bearer {okey}"}
    r = await _submit(fresh, seeded_skill, headers=oh)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "skill_not_allowed_for_key"

    # in-scope submit: 202 + initiator label "owner email (key name)"
    okey2 = await _agent_key(auth_env, o_hdr, "财务终端2", ["invoice_test"])
    oh2 = {"Authorization": f"Bearer {okey2}"}
    r = await _submit(fresh, seeded_skill, headers=oh2)
    assert r.status_code == 202, r.text
    tid = r.json()["transaction_id"]
    r = await auth_env.get("/api/v1/files")
    row = next(x for x in r.json()["data"] if x["transaction_id"] == tid)
    assert row["initiator_type"] == "api_key"
    assert row["initiator_label"] == "op3@example.com（财务终端2）"
    await fresh.aclose()


# —— WP2: submit idempotency ————————————————————————————————————————————

async def test_idempotency_replay_conflict(auth_env, seeded_skill):
    r = await _submit(auth_env, seeded_skill, idem="order-20260917-001")
    assert r.status_code == 202, r.text
    tid = r.json()["transaction_id"]

    # same key + same payload: original transaction handed back, no new row
    r = await _submit(auth_env, seeded_skill, idem="order-20260917-001")
    assert r.status_code == 202
    assert r.json()["transaction_id"] == tid

    # same key + different content: hard conflict
    r = await _submit(auth_env, seeded_skill, blob=b"%PDF-other-content",
                      idem="order-20260917-001")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "idempotency_conflict"

    from app.db import session_factory
    from app.models import Transaction
    async with session_factory()() as s:
        rows = (await s.execute(select(Transaction))).scalars().all()
        assert len(rows) == 1
        assert rows[0].idempotency_key == "order-20260917-001"
        assert rows[0].idem_principal.startswith("user:")
        assert rows[0].request_fingerprint


async def test_idempotency_concurrent_same_key_one_transaction(auth_env, seeded_skill):
    # two racing requests with the same Idempotency-Key: exactly one
    # transaction survives (unique index is the arbiter), both callers get it
    async def call():
        return await _submit(auth_env, seeded_skill, idem="race-1")

    r1, r2 = await asyncio.gather(call(), call())
    assert {r1.status_code, r2.status_code} <= {202}, (r1.text, r2.text)
    assert r1.json()["transaction_id"] == r2.json()["transaction_id"]
    from app.db import session_factory
    from app.models import Transaction
    async with session_factory()() as s:
        rows = (await s.execute(select(Transaction))).scalars().all()
        assert len(rows) == 1


async def test_idempotency_key_too_long(auth_env, seeded_skill):
    r = await _submit(auth_env, seeded_skill, idem="x" * 129)
    assert r.status_code == 400


# —— WP2: /me/api-keys ——————————————————————————————————————————————————

async def test_me_api_keys_rules(auth_env):
    # viewer gets a read-only key; operator gets submit+read scopes
    await _mk_user(auth_env, "view2@example.com", "viewer")
    v_hdr = await _login(auth_env, "view2@example.com", "pw-123456")
    r = await auth_env.post("/api/v1/me/api-keys", headers=v_hdr,
                            json={"name": "v终端"})
    assert r.status_code == 201
    assert r.json()["scopes"] == ["skills:read"]
    assert r.json()["key_type"] == "agent"
    full_v = r.json()["key"]
    id_v = r.json()["id"]
    assert full_v not in (await auth_env.get("/api/v1/me/api-keys", headers=v_hdr)).text

    await _mk_user(auth_env, "op4@example.com", "operator")
    o_hdr = await _login(auth_env, "op4@example.com", "pw-123456")
    r = await auth_env.post("/api/v1/me/api-keys", headers=o_hdr,
                            json={"name": "o终端"})
    assert r.json()["scopes"] == ["process:write", "skills:read"]

    # name validation
    r = await auth_env.post("/api/v1/me/api-keys", headers=o_hdr, json={"name": ""})
    assert r.status_code == 400
    r = await auth_env.post("/api/v1/me/api-keys", headers=o_hdr,
                            json={"name": "x" * 101})
    assert r.status_code == 400

    # one user cannot see or revoke another user's key
    kid = (await auth_env.get("/api/v1/me/api-keys", headers=o_hdr)).json()["keys"][0]["id"]
    vkeys = (await auth_env.get("/api/v1/me/api-keys", headers=v_hdr)).json()["keys"]
    assert [k["name"] for k in vkeys] == ["v终端"]      # only their own
    r = await auth_env.delete(f"/api/v1/me/api-keys/{kid}", headers=v_hdr)
    assert r.status_code == 404

    # revoke: the very next request with the key is a 401
    r = await auth_env.delete(f"/api/v1/me/api-keys/{id_v}", headers=v_hdr)
    assert r.status_code == 204
    fresh = AsyncClient(transport=auth_env._transport, base_url="http://test")
    r = await fresh.post("/api/v1/me/api-keys",
                         headers={"Authorization": f"Bearer {full_v}"}, json={"name": "y"})
    assert r.status_code == 401
    await fresh.aclose()


async def test_admin_issues_ownerless_agent_key(auth_env, seeded_skill):
    # admin-minted keyless-owner agent key: route whitelist applies, submit ok
    r = await auth_env.post("/api/v1/settings/api-keys",
                            json={"name": "车间PC", "key_type": "agent",
                                  "allowed_skill_codes": ["invoice_test"]})
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    assert r.json()["key_type"] == "agent"
    fresh = AsyncClient(transport=auth_env._transport, base_url="http://test")
    ah = {"Authorization": f"Bearer {key}"}
    assert (await fresh.get("/api/v1/agent/ping", headers=ah)).status_code == 200
    r = await _submit(fresh, seeded_skill, headers=ah)
    assert r.status_code == 202, r.text
    tid = r.json()["transaction_id"]
    r = await auth_env.get("/api/v1/files")
    row = next(x for x in r.json()["data"] if x["transaction_id"] == tid)
    # no owner -> label is just the key name
    assert row["initiator_label"] == "车间PC"
    # the admin list shows kind/owner/scope/last-used
    keys = (await auth_env.get("/api/v1/settings/api-keys")).json()
    k = next(x for x in keys if x["name"] == "车间PC")
    assert k["key_type"] == "agent" and k["owner"] is None
    assert k["allowed_skill_codes"] == ["invoice_test"]
    assert k["last_used_at"] is not None
    await fresh.aclose()
