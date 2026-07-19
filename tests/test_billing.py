"""M3 billing closed loop (design v0.2 §12.2/§12.5/§12.7). Live mode:
submit freezes estimated pages x rate (insufficient -> 402), completion
charges actual pages gift-bucket-first and releases the freeze, failures are
never charged. Shadow mode (default) keeps metering only — private
deployments never charge. All through the real API with faked parser/LLM.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, SkillPackage

UDR_1PAGE = UDR(pages=[Page(page_no=1, width=100, height=100, blocks=[
    Block(text="发票号码 INV-2026-001", bbox=[1, 2, 3, 4]),
])], full_markdown="发票号码 INV-2026-001", parser="test")

PKG = SkillPackage(
    skill_code="bill_test", name="计费测试",
    fields=[FieldSpec(name="invoice_no", instruction="发票号码")])


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def booted(tmp_path, monkeypatch, billing_cfg=None, fail_llm=False, auth=False):
    """App on a fresh sqlite with fake parser/LLM; skill published; optional
    billing platform config seeded before any submit. auth=True boots
    IDP_AUTH_MODE=on with a bootstrapped admin and a logged-in client."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    if auth:
        monkeypatch.setenv("IDP_AUTH_MODE", "on")
        monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
        from app.auth import security
        security._secret = None
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from app.parsers import router as prouter
    monkeypatch.setattr(prouter, "parse_document", lambda path, pinned=None: UDR_1PAGE)
    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_1PAGE)

    def fake_extract_llm(messages, chain, transport=None):
        if fail_llm:
            raise RuntimeError("provider down (test)")
        return ({"invoice_no": "INV-2026-001"},
                {"prompt_tokens": 10, "completion_tokens": 5}, "fake")
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_extract_llm)

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            if auth:
                r = await client.post("/api/v1/auth/login",
                                      json={"email": "admin@example.com",
                                            "password": "admin-pass-123"})
                assert r.status_code == 200, r.text
                client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            r = await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            assert r.status_code == 201, r.text
            r = await client.post(f"/api/v1/skills/{PKG.skill_code}/versions/1/publish")
            assert r.status_code == 200, r.text
            if billing_cfg is not None:
                from app.db import session_factory
                from app.models import PlatformSetting
                async with session_factory()() as s:
                    s.add(PlatformSetting(key="billing", value=billing_cfg))
                    await s.commit()
            yield client


async def _set_balance(paid=0.0, gift=0.0, tenant="default"):
    from app.db import session_factory
    from app.models import CreditAccount
    async with session_factory()() as s:
        acct = await s.get(CreditAccount, tenant)
        if acct is None:
            acct = CreditAccount(tenant_id=tenant)
            s.add(acct)
        acct.paid_balance, acct.gift_balance = paid, gift
        await s.commit()


async def _account(tenant="default"):
    from app.db import session_factory
    from app.models import CreditAccount
    async with session_factory()() as s:
        return await s.get(CreditAccount, tenant)


async def _ledger_rows():
    from app.db import session_factory
    from app.models import CreditLedger
    async with session_factory()() as s:
        return (await s.execute(
            select(CreditLedger).order_by(CreditLedger.created_at))).scalars().all()


async def _submit(client):
    return await client.post(
        "/api/v1/process",
        files={"files": ("inv.pdf", b"%PDF-fake", "application/pdf")},
        data={"skill_code": PKG.skill_code})


async def _wait_done(client, tid):
    for _ in range(50):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/v1/status/{tid}")
        if r.json()["status"] in ("completed", "pending_verification", "error"):
            return r.json()
    raise AssertionError("transaction never finished")


LIVE = {"mode": "live", "rates": {"extract": 2.0, "audit": 3.0}}


async def test_live_mode_402_then_topup_then_full_settle(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE) as client:
        # empty account -> 402, nothing persisted (the whole submit rolls back)
        r = await _submit(client)
        assert r.status_code == 402
        assert "余额不足" in r.json()["detail"]
        from app.db import session_factory
        from app.models import Transaction
        async with session_factory()() as s:
            assert (await s.execute(select(Transaction))).scalars().all() == []

        # topup -> submit passes, completion charges actual pages at rate 2.0
        await _set_balance(paid=10.0)
        r = await _submit(client)
        assert r.status_code == 202, r.text
        body = await _wait_done(client, r.json()["transaction_id"])
        assert body["status"] in ("completed", "pending_verification")

        acct = await _account()
        assert acct.frozen == 0.0                     # freeze fully released
        assert acct.paid_balance == pytest.approx(8.0)
        kinds = {(row.kind, row.bucket) for row in await _ledger_rows()}
        assert ("freeze", "paid") in kinds and ("unfreeze", "paid") in kinds
        assert ("charge", "paid") in kinds and ("shadow_meter", "paid") in kinds
        unfreeze = [x for x in await _ledger_rows() if x.kind == "unfreeze"][0]
        assert json.loads(unfreeze.note) == {"pages": 1, "charged": 2.0}


async def test_gift_bucket_burns_first(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE) as client:
        await _set_balance(paid=10.0, gift=1.5)
        r = await _submit(client)
        assert r.status_code == 202, r.text
        await _wait_done(client, r.json()["transaction_id"])

        acct = await _account()
        assert acct.gift_balance == pytest.approx(0.0)      # gift first (§12.7)
        assert acct.paid_balance == pytest.approx(9.5)
        charges = {row.bucket: row.amount for row in await _ledger_rows()
                   if row.kind == "charge"}
        assert charges == {"gift": pytest.approx(1.5), "paid": pytest.approx(0.5)}


async def test_failed_file_unfreezes_and_never_charges(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE, fail_llm=True) as client:
        await _set_balance(paid=10.0)
        r = await _submit(client)
        assert r.status_code == 202, r.text
        body = await _wait_done(client, r.json()["transaction_id"])
        assert body["status"] == "error"

        acct = await _account()                       # 失败不收费 (§12.2 rule 3)
        assert acct.paid_balance == pytest.approx(10.0)
        assert acct.frozen == 0.0
        kinds = [row.kind for row in await _ledger_rows()]
        assert "charge" not in kinds and "unfreeze" in kinds


async def test_shadow_default_meters_but_never_touches_money(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as client:   # no billing config
        r = await _submit(client)
        assert r.status_code == 202, r.text
        await _wait_done(client, r.json()["transaction_id"])
        kinds = {row.kind for row in await _ledger_rows()}
        assert kinds == {"shadow_meter"}              # fact layer only (§12.6)


async def test_unlimited_actor_skips_gate_but_stays_metered(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE) as client:
        import app.api.routes.process as process_mod
        monkeypatch.setattr(process_mod, "current_actor",
                            lambda: {"name": "owner-root", "role": "admin",
                                     "user_id": None, "unlimited": True})
        r = await _submit(client)                     # zero balance, still in
        assert r.status_code == 202, r.text
        await _wait_done(client, r.json()["transaction_id"])
        kinds = {row.kind for row in await _ledger_rows()}
        assert "freeze" not in kinds and "shadow_meter" in kinds   # §12.7: 无限制≠不计量


async def test_settle_is_idempotent(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE) as client:
        await _set_balance(paid=10.0)
        r = await _submit(client)
        tid = r.json()["transaction_id"]
        await _wait_done(client, tid)

        from app.billing.engine import settle
        await settle(tid)                             # redelivery replay
        acct = await _account()
        assert acct.paid_balance == pytest.approx(8.0)  # charged exactly once
        unfreezes = [x for x in await _ledger_rows() if x.kind == "unfreeze"]
        assert len(unfreezes) == 1


async def test_admin_billing_ops_and_gift_dual_control(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as client:
        # rate-card config (platform tier §12.1)
        r = await client.put("/api/v1/billing/config",
                             json={"mode": "live", "gift_review_threshold": 100,
                                   "gift_monthly_cap": 300})
        assert r.status_code == 200 and r.json()["mode"] == "live"

        # topup demands voucher registration (§12.4 对公转账人工确认)
        r = await client.post("/api/v1/billing/topup", json={"amount": 50})
        assert r.status_code == 422
        r = await client.post("/api/v1/billing/topup",
                              json={"amount": 50, "voucher_ref": "BANK-2026-0719-01"})
        assert r.status_code == 201 and r.json()["available"] == pytest.approx(50)

        # signed adjustment, reason mandatory
        r = await client.post("/api/v1/billing/adjust",
                              json={"amount": -10, "reason": "对账修正"})
        assert r.status_code == 201 and r.json()["available"] == pytest.approx(40)

        # small gift auto-approves and lands in the gift bucket
        r = await client.post("/api/v1/billing/gift",
                              json={"amount": 80, "campaign": "launch", "reason": "试用"})
        assert r.status_code == 201 and r.json()["status"] == "approved"
        assert (await _account()).gift_balance == pytest.approx(80)

        # above threshold -> pending; requester cannot self-approve (双人复核)
        r = await client.post("/api/v1/billing/gift",
                              json={"amount": 150, "campaign": "launch", "reason": "大客户"})
        rid = r.json()["request_id"]
        assert r.json()["status"] == "pending"
        assert (await _account()).gift_balance == pytest.approx(80)   # no money yet
        r = await client.post(f"/api/v1/billing/gift-requests/{rid}/approve")
        assert r.status_code == 403

        # a SECOND admin approves -> money moves exactly once
        import app.api.routes.billing as broutes
        import app.tenancy as tenancy
        monkeypatch.setattr(broutes, "current_actor",
                            lambda: {"name": "second-admin", "role": "admin",
                                     "user_id": None})
        r = await client.post(f"/api/v1/billing/gift-requests/{rid}/approve")
        assert r.status_code == 200 and r.json()["status"] == "approved"
        monkeypatch.setattr(broutes, "current_actor", tenancy.current_actor)
        assert (await _account()).gift_balance == pytest.approx(230)
        r = await client.post(f"/api/v1/billing/gift-requests/{rid}/approve")
        assert r.status_code == 409                                   # already decided

        # per-operator monthly ceiling: 80+150 granted, cap 300 -> another 100 fails
        r = await client.post("/api/v1/billing/gift",
                              json={"amount": 100, "campaign": "launch", "reason": "x"})
        assert r.status_code == 400 and "上限" in r.json()["detail"]

        # ledger carries the full trail
        kinds = [row.kind for row in await _ledger_rows()]
        assert kinds.count("topup") == 1 and kinds.count("adjust") == 1
        assert kinds.count("gift") == 2


async def test_cross_tenant_topup_and_platform_guard(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as client:
        # platform admin funds a customer tenant's account
        r = await client.post("/api/v1/billing/topup",
                              json={"tenant_id": "cust1", "amount": 20,
                                    "voucher_ref": "BANK-X"})
        assert r.status_code == 201
        assert (await _account("cust1")).paid_balance == pytest.approx(20)

        # money ops are refused outside the platform tenant
        import app.api.routes.billing as broutes
        monkeypatch.setattr(broutes, "current_tenant", lambda: "cust1")
        r = await client.put("/api/v1/billing/config", json={"mode": "live"})
        assert r.status_code == 403


async def test_allocated_key_pays_from_its_own_budget(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch, billing_cfg=LIVE, auth=True) as client:
        await _set_balance(paid=100.0)

        # mint a key, switch it to allocated, carve out 3 credits
        r = await client.post("/api/v1/settings/api-keys", json={"name": "erp"})
        key_id, full_key = r.json()["id"], r.json()["api_key"]
        r = await client.put(f"/api/v1/settings/api-keys/{key_id}/quota",
                             json={"mode": "allocated"})
        assert r.status_code == 200
        r = await client.post(f"/api/v1/settings/api-keys/{key_id}/allocate",
                              json={"amount": 3})
        assert r.status_code == 200, r.text
        assert r.json()["allocated_balance"] == pytest.approx(3)
        assert (await _account()).paid_balance == pytest.approx(97)

        # the key submits: charged against ITS budget, tenant pool untouched
        key_client = AsyncClient(transport=client._transport, base_url="http://test",
                                 headers={"Authorization": f"Bearer {full_key}"})
        r = await key_client.post(
            "/api/v1/process",
            files={"files": ("inv.pdf", b"%PDF-fake", "application/pdf")},
            data={"skill_code": PKG.skill_code})
        assert r.status_code == 202, r.text
        await _wait_done(key_client, r.json()["transaction_id"])
        r = await client.get("/api/v1/settings/api-keys")
        k = [x for x in r.json() if x["id"] == key_id][0]
        assert k["allocated_balance"] == pytest.approx(1)      # 3 - 1page*2.0
        assert k["allocated_frozen"] == pytest.approx(0)
        assert (await _account()).paid_balance == pytest.approx(97)

        # drained key 402s ON ITS OWN — the tenant pool stays available (§12.7)
        r = await key_client.post(
            "/api/v1/process",
            files={"files": ("inv.pdf", b"%PDF-fake", "application/pdf")},
            data={"skill_code": PKG.skill_code})
        assert r.status_code == 402 and "API Key" in r.json()["detail"]
        # pool control via a NORMAL operator (the bootstrap admin is Owner
        # Root/unlimited and would bypass the gate instead of proving the pool)
        r = await client.post("/api/v1/auth/users",
                              json={"email": "op@example.com",
                                    "password": "operator-pw-1", "role": "operator"})
        assert r.status_code == 201, r.text
        r = await client.post("/api/v1/auth/login",
                              json={"email": "op@example.com",
                                    "password": "operator-pw-1"})
        op_client = AsyncClient(
            transport=client._transport, base_url="http://test",
            headers={"Authorization": f"Bearer {r.json()['access_token']}"})
        r = await _submit(op_client)                           # operator -> pool
        assert r.status_code == 202, r.text
        await _wait_done(op_client, r.json()["transaction_id"])
        await op_client.aclose()

        # reclaim the remainder back into the pool
        r = await client.post(f"/api/v1/settings/api-keys/{key_id}/allocate",
                              json={"amount": -1})
        assert r.status_code == 200
        assert r.json()["allocated_balance"] == pytest.approx(0)
        acct = await _account()
        assert acct.paid_balance == pytest.approx(96)          # 97 - 2(pool job) + 1
        r = await client.post(f"/api/v1/settings/api-keys/{key_id}/allocate",
                              json={"amount": -5})
        assert r.status_code == 400                            # nothing left to reclaim
        await key_client.aclose()


def test_estimate_pages():
    from pypdf import PdfWriter

    from app.billing.engine import estimate_pages
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=100, height=100)
    buf = BytesIO()
    w.write(buf)
    assert estimate_pages(buf.getvalue(), ".pdf") == 3
    assert estimate_pages(b"not a pdf", ".pdf") == 1  # unparseable -> floor of 1
    assert estimate_pages(b"\xff\xd8jpeg", ".jpg") == 1
