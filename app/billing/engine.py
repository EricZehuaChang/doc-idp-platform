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
from app.models import CreditAccount, CreditLedger, FileRecord, PlatformSetting, TenantSetting

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


class InsufficientCredit(Exception):
    """Submit-time balance gate failure -> HTTP 402 at the API edge."""

    def __init__(self, required: float, available: float):
        self.required, self.available = required, available
        super().__init__(f"required {required}, available {available}")


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
                 pages_est: int, rate: float) -> None:
    """Reserve estimated_pages x rate inside the caller's submit transaction —
    the freeze commits (or rolls back) together with the Transaction row.
    Raises InsufficientCredit when the guarded UPDATE matches no row."""
    amount = round(pages_est * rate, 4)
    acct = await ensure_account(s, tenant_id)
    if amount <= 0:
        return
    res = await s.execute(
        update(CreditAccount)
        .where(CreditAccount.tenant_id == tenant_id,
               CreditAccount.paid_balance + CreditAccount.gift_balance
               - CreditAccount.frozen >= amount)
        .values(frozen=CreditAccount.frozen + amount))
    # the core UPDATE bypassed the identity map: expire ONLY the account object
    # (expire_all would poison the caller's objects, e.g. the Transaction row)
    s.expire(acct)
    if res.rowcount == 0:
        raise InsufficientCredit(amount, await available(s, tenant_id))
    s.add(CreditLedger(
        tenant_id=tenant_id, kind="freeze", amount=amount,
        transaction_id=transaction_id, idempotency_key=f"freeze:{transaction_id}",
        note=json.dumps({"rate": rate, "pages_est": pages_est}),
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
