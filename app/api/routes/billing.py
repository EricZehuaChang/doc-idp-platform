"""Billing admin API (design v0.2 §12.4/§12.7): account/ledger visibility for
tenant admins; money movements (manual topup with voucher registration,
signed adjustments, marketing gifts) are PLATFORM-operator actions — domestic
enterprise reality is bank transfer + human confirmation, so the recharge
backend is the first payment channel (§12.4).

Dual control on gifts (§12.7): every grant attempt is a GiftRequest row;
amounts above the review threshold wait for a SECOND admin, and each operator
has a monthly grant ceiling. All movements land in the append-only ledger and
the audit log.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.billing import engine as billing
from app.config import get_settings
from app.db import session_factory
from app.models import (AuditLog, CreditAccount, CreditLedger, GiftRequest,
                        PlatformSetting, TenantSetting)
from app.tenancy import as_tenant, current_actor, current_tenant, has_role

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _require_admin() -> None:
    if not has_role("admin"):
        raise HTTPException(403, "admin role required")


def _require_platform_admin() -> str:
    """Money ops belong to the platform operator: admin role AND the platform
    tenant. A customer-tenant admin topping up their own account for free
    would be a hole, not a feature. Returns the acting tenant."""
    _require_admin()
    tenant = current_tenant()
    if tenant != get_settings().default_tenant:
        raise HTTPException(403, "platform operator only")
    return tenant


def _target(tenant_id: str | None) -> str:
    return tenant_id or current_tenant()


async def _audit(action: str, detail: dict) -> None:
    sf = session_factory()
    async with sf() as s:
        s.add(AuditLog(tenant_id=current_tenant(), actor=current_actor()["name"],
                       action=action, detail=detail))
        await s.commit()


# —— visibility (tenant admin, own account) ——————————————————————————————

@router.get("/account")
async def get_account(tenant_id: str | None = None):
    _require_admin()
    if tenant_id and tenant_id != current_tenant():
        _require_platform_admin()
    target = _target(tenant_id)
    sf = session_factory()
    async with sf() as s:
        acct = await s.get(CreditAccount, target)
        cfg = await billing.load_config(s)
        byok = await billing.is_byok_tenant(s, target)
        plan_name, _ = await billing.tenant_plan(s, target)
    paid = acct.paid_balance if acct else 0.0
    gift = acct.gift_balance if acct else 0.0
    frozen = acct.frozen if acct else 0.0
    return {"tenant_id": target, "plan": plan_name,
            "paid_balance": round(paid, 4),
            "gift_balance": round(gift, 4), "frozen": round(frozen, 4),
            "available": round(paid + gift - frozen, 4),
            "mode": cfg["mode"], "byok": byok,
            "rates": cfg["rates"], "rates_byok": cfg["rates_byok"],
            "gift_review_threshold": cfg["gift_review_threshold"],
            "gift_monthly_cap": cfg["gift_monthly_cap"]}


@router.get("/ledger")
async def get_ledger(tenant_id: str | None = None, kind: str | None = None,
                     limit: int = 50, offset: int = 0):
    _require_admin()
    if tenant_id and tenant_id != current_tenant():
        _require_platform_admin()
    target = _target(tenant_id)
    limit = max(1, min(limit, 200))
    sf = session_factory()
    async with sf() as s:
        q = select(CreditLedger).where(CreditLedger.tenant_id == target)
        cq = select(func.count()).select_from(CreditLedger).where(
            CreditLedger.tenant_id == target)
        if kind:
            q = q.where(CreditLedger.kind == kind)
            cq = cq.where(CreditLedger.kind == kind)
        total = (await s.execute(cq)).scalar_one()
        rows = (await s.execute(q.order_by(CreditLedger.created_at.desc())
                                .limit(limit).offset(offset))).scalars().all()
    return {"total": total, "items": [
        {"id": r.id, "kind": r.kind, "bucket": r.bucket, "amount": round(r.amount, 4),
         "transaction_id": r.transaction_id, "note": r.note,
         "balance_snapshot": r.balance_snapshot,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows]}


# —— platform billing config (§12.1 rate card) ———————————————————————————

class ConfigBody(BaseModel):
    mode: str | None = None                      # shadow | live
    rates: dict[str, float] | None = None
    rates_byok: dict[str, float] | None = None
    gift_review_threshold: float | None = Field(None, ge=0)
    gift_monthly_cap: float | None = Field(None, ge=0)


@router.put("/config")
async def put_config(body: ConfigBody):
    _require_platform_admin()
    if body.mode is not None and body.mode not in ("shadow", "live"):
        raise HTTPException(400, "mode must be shadow|live")
    for card in (body.rates, body.rates_byok):
        if card and any(v < 0 for v in card.values()):
            raise HTTPException(400, "rates must be >= 0")
    sf = session_factory()
    async with sf() as s:
        cfg = await billing.load_config(s)
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        cfg.update(patch)
        row = await s.get(PlatformSetting, billing.SETTING_KEY)
        if row is None:
            s.add(PlatformSetting(key=billing.SETTING_KEY, value=cfg))
        else:
            row.value = cfg
        await s.commit()
    await _audit("billing.config_updated", patch)
    return cfg


# —— plan entitlements (§12.3): templates + tenant assignment —————————————

class PlansBody(BaseModel):
    plans: dict[str, dict[str, int]]


@router.get("/plans")
async def get_plans():
    _require_admin()
    sf = session_factory()
    async with sf() as s:
        plans = await billing.load_plans(s)
        name, ent = await billing.tenant_plan(s, current_tenant())
    return {"plans": plans, "tenant_plan": name, "entitlements": ent}


@router.put("/plans")
async def put_plans(body: PlansBody):
    """Edit plan templates (merged over defaults). Entitlement values are
    counts; a key left out of a plan means no cap on that item."""
    _require_platform_admin()
    for plan, ent in body.plans.items():
        if any(v < 0 for v in ent.values()):
            raise HTTPException(400, f"plan {plan}: entitlements must be >= 0")
    sf = session_factory()
    async with sf() as s:
        plans = await billing.load_plans(s)
        plans.update(body.plans)
        row = await s.get(PlatformSetting, billing.PLANS_KEY)
        if row is None:
            s.add(PlatformSetting(key=billing.PLANS_KEY, value=plans))
        else:
            row.value = plans
        await s.commit()
    await _audit("billing.plans_updated", {"plans": list(body.plans)})
    return {"plans": plans}


class PlanAssignBody(BaseModel):
    tenant_id: str | None = None
    plan: str | None = None                      # None clears (uncapped)


@router.put("/plan")
async def assign_plan(body: PlanAssignBody):
    _require_platform_admin()
    target = _target(body.tenant_id)
    with as_tenant(target):              # RLS: the row belongs to the target
        sf = session_factory()
        async with sf() as s:
            if body.plan is not None and body.plan not in await billing.load_plans(s):
                raise HTTPException(400, f"unknown plan: {body.plan}")
            row = (await s.execute(
                select(TenantSetting).where(TenantSetting.tenant_id == target,
                                            TenantSetting.key == "plan"))).scalar_one_or_none()
            value = {"name": body.plan} if body.plan else {}
            if row is None:
                s.add(TenantSetting(tenant_id=target, key="plan", value=value))
            else:
                row.value = value
            await s.commit()
    await _audit("billing.plan_assigned", {"tenant_id": target, "plan": body.plan})
    return {"tenant_id": target, "plan": body.plan}


# —— manual money movements (platform operator, §12.4) ————————————————————

async def _move(target: str, kind: str, bucket: str, amount: float,
                note: dict, idempotency_key: str | None = None) -> float:
    """Apply a signed balance movement + ledger row in the TARGET tenant's RLS
    scope. Returns available-after. Caller writes the audit row."""
    col = (CreditAccount.paid_balance if bucket == "paid"
           else CreditAccount.gift_balance)
    field = "paid_balance" if bucket == "paid" else "gift_balance"
    with as_tenant(target):
        sf = session_factory()
        async with sf() as s:
            acct = await billing.ensure_account(s, target)
            await s.execute(update(CreditAccount)
                            .where(CreditAccount.tenant_id == target)
                            .values(**{field: col + amount}))
            s.expire(acct)
            after = await billing.available(s, target)
            s.add(CreditLedger(tenant_id=target, kind=kind, bucket=bucket,
                               amount=amount, note=json.dumps(note, ensure_ascii=False),
                               idempotency_key=idempotency_key,
                               balance_snapshot=after))
            await s.commit()
    return after


class TopupBody(BaseModel):
    tenant_id: str | None = None
    amount: float = Field(gt=0)
    voucher_ref: str = Field(min_length=1, max_length=200)   # 凭证登记 (§12.4)
    note: str = ""


@router.post("/topup", status_code=201)
async def topup(body: TopupBody):
    _require_platform_admin()
    target = _target(body.tenant_id)
    after = await _move(target, "topup", "paid", body.amount,
                        {"voucher_ref": body.voucher_ref, "note": body.note,
                         "by": current_actor()["name"]})
    await _audit("billing.topup", {"tenant_id": target, "amount": body.amount,
                                   "voucher_ref": body.voucher_ref})
    return {"tenant_id": target, "available": after}


class AdjustBody(BaseModel):
    tenant_id: str | None = None
    amount: float                                # signed; clawbacks may go negative
    bucket: str = "paid"
    reason: str = Field(min_length=1, max_length=500)   # mandatory (§12.6 audit rule)


@router.post("/adjust", status_code=201)
async def adjust(body: AdjustBody):
    _require_platform_admin()
    if body.bucket not in ("paid", "gift"):
        raise HTTPException(400, "bucket must be paid|gift")
    if body.amount == 0:
        raise HTTPException(400, "amount must be non-zero")
    target = _target(body.tenant_id)
    after = await _move(target, "adjust", body.bucket, body.amount,
                        {"reason": body.reason, "by": current_actor()["name"]})
    await _audit("billing.adjust", {"tenant_id": target, "amount": body.amount,
                                    "bucket": body.bucket, "reason": body.reason})
    return {"tenant_id": target, "available": after}


# —— marketing gifts with dual control (§12.7) ————————————————————————————

class GiftBody(BaseModel):
    tenant_id: str | None = None
    amount: float = Field(gt=0)
    campaign: str = Field(min_length=1, max_length=100)   # activity tag mandatory
    reason: str = Field(min_length=1, max_length=500)


async def _apply_gift(req: GiftRequest) -> None:
    await _move(req.target_tenant_id, "gift", "gift", req.amount,
                {"campaign": req.campaign, "request_id": req.id},
                idempotency_key=f"gift:{req.id}")


@router.post("/gift", status_code=201)
async def gift(body: GiftBody):
    _require_platform_admin()
    actor = current_actor()["name"]
    target = _target(body.tenant_id)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sf = session_factory()
    async with sf() as s:
        cfg = await billing.load_config(s)
        granted = (await s.execute(
            select(func.coalesce(func.sum(GiftRequest.amount), 0.0))
            .where(GiftRequest.requested_by == actor,
                   GiftRequest.status != "rejected",
                   GiftRequest.created_at >= month_start))).scalar_one()
        if granted + body.amount > cfg["gift_monthly_cap"]:
            raise HTTPException(400, f"操作员本月赠送额度已达上限"
                                f"({cfg['gift_monthly_cap']:g}),本月已赠 {granted:g}")
        needs_review = body.amount > cfg["gift_review_threshold"]
        req = GiftRequest(tenant_id=current_tenant(), target_tenant_id=target,
                          amount=body.amount, campaign=body.campaign,
                          reason=body.reason, requested_by=actor,
                          status="pending" if needs_review else "approved",
                          decided_by=None if needs_review else actor,
                          decided_at=None if needs_review else now)
        s.add(req)
        await s.commit()
        req_id, status = req.id, req.status
        if not needs_review:
            await _apply_gift(req)
    await _audit("billing.gift_requested",
                 {"request_id": req_id, "tenant_id": target, "amount": body.amount,
                  "campaign": body.campaign, "auto_approved": not needs_review})
    return {"request_id": req_id, "status": status}


@router.get("/gift-requests")
async def gift_requests(status: str | None = None):
    _require_platform_admin()
    sf = session_factory()
    async with sf() as s:
        q = select(GiftRequest).where(GiftRequest.tenant_id == current_tenant())
        if status:
            q = q.where(GiftRequest.status == status)
        rows = (await s.execute(q.order_by(GiftRequest.created_at.desc())
                                .limit(200))).scalars().all()
    return [{"id": r.id, "target_tenant_id": r.target_tenant_id,
             "amount": r.amount, "campaign": r.campaign, "reason": r.reason,
             "requested_by": r.requested_by, "status": r.status,
             "decided_by": r.decided_by,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@router.post("/gift-requests/{request_id}/{decision}")
async def decide_gift(request_id: str, decision: str):
    """Dual control: the decider must be a DIFFERENT admin than the requester."""
    _require_platform_admin()
    if decision not in ("approve", "reject"):
        raise HTTPException(404, "decision must be approve|reject")
    actor = current_actor()["name"]
    sf = session_factory()
    async with sf() as s:
        req = await s.get(GiftRequest, request_id)
        if req is None or req.tenant_id != current_tenant():
            raise HTTPException(404, "gift request not found")
        if req.status != "pending":
            raise HTTPException(409, f"request already {req.status}")
        if decision == "approve" and req.requested_by == actor:
            raise HTTPException(403, "双人复核:发起人不能自批")
        req.status = "approved" if decision == "approve" else "rejected"
        req.decided_by = actor
        req.decided_at = datetime.now(timezone.utc)
        await s.commit()
        if req.status == "approved":
            await _apply_gift(req)
    await _audit(f"billing.gift_{req.status}",
                 {"request_id": request_id, "tenant_id": req.target_tenant_id,
                  "amount": req.amount})
    return {"request_id": request_id, "status": req.status}
