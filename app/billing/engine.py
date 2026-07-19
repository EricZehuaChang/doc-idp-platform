"""Billing engine (design v0.2 §12): the freeze -> charge -> unfreeze money
lifecycle on top of the M1 shadow-metering foundation.

Two layers stay separate by design (§12.6): metering (shadow_meter, always on)
is the FACT layer feeding usage dashboards and pricing calibration; charging
(this module) is the POLICY layer and only acts in live mode. Private
deployments keep mode=shadow — license-based pricing meters but never charges
(§12.5); SaaS flips to live.

Money rules (§12.2):
- submit freezes estimated_pages x rate; insufficient available -> 402 (the
  caller raises; 402 = "buy more", distinct from 429 rate limiting);
- completion charges ACTUAL pages (gift bucket first, §12.7) and releases the
  full freeze; failed files are never charged ("失败不收费" is a commercial
  term commitment);
- every movement is an append-only CreditLedger row; freeze/settle are
  idempotent per transaction via unique idempotency keys, so queue redelivery
  can never double-charge (HA discipline v2.0 §2.4);
- concurrency: balance math is guarded atomic UPDATEs (WHERE available >= x),
  never read-modify-write on the account row.
"""
import json
import logging
from io import BytesIO

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import session_factory
from app.models import (ApiKey, CreditAccount, CreditLedger, FileRecord,
                        PlatformSetting, TenantSetting)

log = logging.getLogger("idp.billing")

SETTING_KEY = "billing"

# rate card defaults (§12.1): credits per page by skill kind, platform-pool vs
# BYOK columns (BYOK tenants pay their own model bills -> discounted platform fee)
DEFAULTS = {
    "mode": "shadow",                     # shadow | live
    "rates": {"extract": 1.0, "audit": 2.0},
    "rates_byok": {"extract": 0.5, "audit": 1.0},
    "gift_review_threshold": 1000.0,      # gifts above this need a second admin (§12.7)
    "gift_monthly_cap": 10000.0,          # per-operator monthly grant ceiling (§12.7)
}


PLANS_KEY = "plans"

# plan templates (§12.3 Starter/Pro/Enterprise): entitlement numbers are
# business config — these defaults are placeholders until pricing lands (M5).
# A missing entitlement key means "no cap"; a tenant with NO plan assigned is
# uncapped (private/POC deployments never hit artificial walls).
PLAN_DEFAULTS = {
    "starter": {"max_members": 5, "max_skills": 10},
    "pro": {"max_members": 50, "max_skills": 100},
    "enterprise": {},
}


class EntitlementExceeded(Exception):
    """Plan cap hit -> HTTP 403 at the API edge with an upgrade hint."""

    def __init__(self, plan: str, item: str, limit: int):
        self.plan, self.item, self.limit = plan, item, limit
        super().__init__(f"{item} limit {limit} reached on plan {plan}")


class InsufficientCredit(Exception):
    """Submit-time balance gate failure -> HTTP 402 at the API edge.
    payer tells the edge whose budget ran dry: "tenant" pool or an
    allocated API "key" (whose 402 must not implicate sibling keys, §12.7)."""

    def __init__(self, required: float, available: float, payer: str = "tenant"):
        self.required, self.available, self.payer = required, available, payer
        super().__init__(f"required {required}, available {available} ({payer})")


async def load_config(s) -> dict:
    """Billing platform config (§11.10 platform tier) over defaults."""
    row = await s.get(PlatformSetting, SETTING_KEY)
    cfg = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        cfg.update(row.value)
    return cfg


async def is_byok_tenant(s, tenant_id: str) -> bool:
    """BYOK rate column applies when the tenant configured any provider key."""
    row = (await s.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant_id,
                                    TenantSetting.key == "byok"))).scalar_one_or_none()
    return bool(row and any(
        isinstance(v, dict) and v.get("api_key_enc") for v in (row.value or {}).values()))


async def load_plans(s) -> dict:
    row = await s.get(PlatformSetting, PLANS_KEY)
    plans = dict(PLAN_DEFAULTS)
    if row and isinstance(row.value, dict):
        plans.update(row.value)
    return plans


async def tenant_plan(s, tenant_id: str) -> tuple[str | None, dict]:
    """(plan_name, entitlements) for a tenant; (None, {}) = uncapped."""
    row = (await s.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant_id,
                                    TenantSetting.key == "plan"))).scalar_one_or_none()
    name = (row.value or {}).get("name") if row else None
    if not name:
        return None, {}
    return name, (await load_plans(s)).get(name, {})


async def enforce_member_cap(s, tenant_id: str) -> None:
    """Raise EntitlementExceeded when adding one more member would break the
    plan. Counts ACTIVE users only — offboarding (人走号停) frees the seat."""
    from sqlalchemy import func

    from app.models import User
    name, ent = await tenant_plan(s, tenant_id)
    limit = ent.get("max_members")
    if limit is None:
        return
    n = (await s.execute(select(func.count()).select_from(User)
                         .where(User.tenant_id == tenant_id,
                                User.active))).scalar_one()
    if n >= limit:
        raise EntitlementExceeded(name, "成员数", limit)


async def enforce_skill_cap(s, tenant_id: str) -> None:
    from sqlalchemy import func

    from app.models import Skill
    name, ent = await tenant_plan(s, tenant_id)
    limit = ent.get("max_skills")
    if limit is None:
        return
    n = (await s.execute(select(func.count()).select_from(Skill)
                         .where(Skill.tenant_id == tenant_id,
                                Skill.state != "deleted"))).scalar_one()
    if n >= limit:
        raise EntitlementExceeded(name, "技能数", limit)


def rate_for(cfg: dict, skill_kind: str, byok: bool) -> float:
    column = cfg.get("rates_byok" if byok else "rates") or {}
    fallback = DEFAULTS["rates_byok" if byok else "rates"]
    return float(column.get(skill_kind, fallback.get(skill_kind, 1.0)))


def estimate_pages(blob: bytes, suffix: str) -> int:
    """Submit-time page estimate for the freeze amount. PDFs count real pages;
    everything else (images, OFD) estimates 1 — settle charges ACTUAL pages, so
    an under-estimate only shrinks the freeze, never the bill."""
    if suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return max(len(PdfReader(BytesIO(blob)).pages), 1)
        except Exception:
            return 1
    return 1


async def ensure_account(s, tenant_id: str) -> CreditAccount:
    acct = await s.get(CreditAccount, tenant_id)
    if acct is None:
        acct = CreditAccount(tenant_id=tenant_id)
        s.add(acct)
        await s.flush()
    return acct


async def available(s, tenant_id: str) -> float:
    acct = await s.get(CreditAccount, tenant_id)
    return (acct.paid_balance + acct.gift_balance - acct.frozen) if acct else 0.0


async def freeze(s, tenant_id: str, transaction_id: str,
                 pages_est: int, rate: float, key_id: str | None = None) -> None:
    """Reserve estimated_pages x rate inside the caller's submit transaction —
    the freeze commits (or rolls back) together with the Transaction row.
    key_id switches the payer to an allocated API key's own budget (§12.7).
    Raises InsufficientCredit when the guarded UPDATE matches no row."""
    amount = round(pages_est * rate, 4)
    meta: dict = {"rate": rate, "pages_est": pages_est}

    if key_id is not None:
        key = await s.get(ApiKey, key_id)
        if amount > 0:
            res = await s.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id,
                       ApiKey.allocated_balance - ApiKey.allocated_frozen >= amount)
                .values(allocated_frozen=ApiKey.allocated_frozen + amount))
            s.expire(key)
            if res.rowcount == 0:
                key = await s.get(ApiKey, key_id)
                free = (key.allocated_balance - key.allocated_frozen) if key else 0.0
                raise InsufficientCredit(amount, round(free, 4), payer="key")
        meta["payer"] = f"key:{key_id}"
    else:
        acct = await ensure_account(s, tenant_id)
        if amount <= 0:
            return
        res = await s.execute(
            update(CreditAccount)
            .where(CreditAccount.tenant_id == tenant_id,
                   CreditAccount.paid_balance + CreditAccount.gift_balance
                   - CreditAccount.frozen >= amount)
            .values(frozen=CreditAccount.frozen + amount))
        # the core UPDATE bypassed the identity map: expire ONLY the account
        # object (expire_all would poison the caller's objects, e.g. the
        # Transaction row)
        s.expire(acct)
        if res.rowcount == 0:
            raise InsufficientCredit(amount, await available(s, tenant_id))

    if amount <= 0:
        return
    s.add(CreditLedger(
        tenant_id=tenant_id, kind="freeze", amount=amount,
        transaction_id=transaction_id, idempotency_key=f"freeze:{transaction_id}",
        note=json.dumps(meta),
        balance_snapshot=await available(s, tenant_id)))


async def settle(transaction_id: str) -> None:
    """Post-finalize settlement: charge actual billable pages at the rate
    captured at freeze time, release the full freeze. No freeze row (shadow
    mode / unlimited submitter / grandfathered in-flight) -> no-op. Idempotent:
    the unfreeze row carries the unique settle:{txn} key; a concurrent
    duplicate loses on the unique constraint and rolls back."""
    sf = session_factory()
    async with sf() as s:
        frow = (await s.execute(select(CreditLedger).where(
            CreditLedger.idempotency_key == f"freeze:{transaction_id}"))).scalar_one_or_none()
        if frow is None:
            return
        done = (await s.execute(select(CreditLedger.id).where(
            CreditLedger.idempotency_key == f"settle:{transaction_id}"))).first()
        if done:
            return
        # capture freeze-row fields as locals before any expire: touching an
        # expired ORM attribute sync-refreshes and breaks the async session
        meta = json.loads(frow.note or "{}")
        rate = float(meta.get("rate", 1.0))
        tenant = frow.tenant_id
        freeze_amount = frow.amount

        # billable = files that produced a result; error rows are free, split
        # parents sit in status "split" and their children carry the pages
        pages = 0
        rows = (await s.execute(select(FileRecord.status, FileRecord.page_count)
                                .where(FileRecord.transaction_id == transaction_id))).all()
        for status, page_count in rows:
            if status in ("completed", "pending_verification", "passed"):
                pages += page_count or 0
        charge_total = round(pages * rate, 4)

        # allocated-key payer (§12.7): the key's own budget absorbs the charge;
        # no gift bucket — key budget is carved from the paid pool.
        payer = str(meta.get("payer") or "")
        if payer.startswith("key:"):
            key_id = payer[4:]
            key = await s.get(ApiKey, key_id)
            res = await s.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id, ApiKey.allocated_frozen >= freeze_amount)
                .values(allocated_balance=ApiKey.allocated_balance - charge_total,
                        allocated_frozen=ApiKey.allocated_frozen - freeze_amount))
            if key is not None:
                s.expire(key)
            if res.rowcount == 0:
                log.error("settle: key budget update failed txn=%s key=%s",
                          transaction_id, key_id)
                return
            key = await s.get(ApiKey, key_id)
            snapshot = round(key.allocated_balance - key.allocated_frozen, 4)
            if charge_total > 0:
                s.add(CreditLedger(tenant_id=tenant, kind="charge", bucket="paid",
                                   amount=charge_total, transaction_id=transaction_id,
                                   note=json.dumps({"payer": payer})))
            s.add(CreditLedger(
                tenant_id=tenant, kind="unfreeze", amount=freeze_amount,
                transaction_id=transaction_id,
                idempotency_key=f"settle:{transaction_id}",
                note=json.dumps({"pages": pages, "charged": charge_total,
                                 "payer": payer}),
                balance_snapshot=snapshot))
            try:
                await s.commit()
            except IntegrityError:       # concurrent settle won the unique key
                await s.rollback()
                return
            log.info("settled txn=%s pages=%s charged=%s payer=%s",
                     transaction_id, pages, charge_total, payer)
            return

        # bucket split: gift burns first (§12.7). The guarded UPDATE re-checks
        # the gift amount; a racing balance change retries with fresh numbers.
        for attempt in range(3):
            acct = await s.get(CreditAccount, tenant)
            if acct is None:
                log.error("settle: no account for tenant=%s txn=%s", tenant, transaction_id)
                return
            gift_use = round(min(max(acct.gift_balance, 0.0), charge_total), 4)
            paid_use = round(charge_total - gift_use, 4)
            res = await s.execute(
                update(CreditAccount)
                .where(CreditAccount.tenant_id == tenant,
                       CreditAccount.gift_balance >= gift_use,
                       CreditAccount.frozen >= freeze_amount)
                .values(gift_balance=CreditAccount.gift_balance - gift_use,
                        paid_balance=CreditAccount.paid_balance - paid_use,
                        frozen=CreditAccount.frozen - freeze_amount))
            s.expire(acct)               # core UPDATE bypassed the identity map
            if res.rowcount:
                break
        else:
            log.error("settle: balance update failed txn=%s (frozen mismatch?)", transaction_id)
            return

        if gift_use > 0:
            s.add(CreditLedger(tenant_id=tenant, kind="charge", bucket="gift",
                               amount=gift_use, transaction_id=transaction_id))
        if paid_use > 0:
            s.add(CreditLedger(tenant_id=tenant, kind="charge", bucket="paid",
                               amount=paid_use, transaction_id=transaction_id))
        s.add(CreditLedger(
            tenant_id=tenant, kind="unfreeze", amount=freeze_amount,
            transaction_id=transaction_id, idempotency_key=f"settle:{transaction_id}",
            note=json.dumps({"pages": pages, "charged": charge_total}),
            balance_snapshot=await available(s, tenant)))
        try:
            await s.commit()
        except IntegrityError:           # concurrent settle won the unique key
            await s.rollback()
            return
    log.info("settled txn=%s pages=%s charged=%s", transaction_id, pages, charge_total)
