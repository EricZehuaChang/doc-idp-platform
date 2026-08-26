"""Data & analytics API (design v0.2 §7 groups 4-5):
- Home: metric strip + the full task ledger (Insavlo home shape)
- Cabinet: structured results of decided files, per skill, JSON or CSV export
- Stats: skill quality metrics from day-one data (fill rate / correction rate /
  straight-through rate — PM item #1, the "how accurate is it" answer)
"""
import csv
import io
import json
from datetime import datetime, time, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, func, or_, select

from app.db import session_factory
from app.models import CreditAccount, CreditLedger, FileRecord, Correction, Transaction
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1", tags=["data"])

_DONE = ("passed", "completed", "exported")


def _day_bounds(date_from: str | None, date_to: str | None):
    """Inclusive `YYYY-MM-DD` day range -> half-open UTC datetime bounds.
    `date_to` covers the whole day, so 08-26..08-26 returns that day's tasks."""
    lo = hi = None
    if date_from:
        lo = datetime.combine(datetime.fromisoformat(date_from).date(), time.min,
                              tzinfo=timezone.utc)
    if date_to:
        hi = datetime.combine(datetime.fromisoformat(date_to).date(), time.max,
                              tzinfo=timezone.utc)
    return lo, hi


def _like(term: str) -> str:
    """LIKE pattern for a literal substring: a filename with `_` or `%` must be
    searched literally, not read as a wildcard."""
    esc = term.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{esc}%"


@router.get("/files")
async def list_files(status: str | None = None, q: str | None = None,
                     skill_code: str | None = None,
                     file_name: str | None = None, file_type: str | None = None,
                     pages_min: int | None = None, pages_max: int | None = None,
                     verify: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     updated_from: str | None = None, updated_to: str | None = None,
                     page: int = 1, page_size: int = 20):
    """Task ledger for the Home page: every file, newest first, paged — the
    Insavlo home-table shape.

    Narrowing (P09, 2026-08-26) is server-side on purpose: the console pages at
    20 rows, so filtering in the browser would only ever search the page in hand
    and report a total that disagrees with what is on screen. Every column of
    the task table has a matching parameter here, so the UI can put its filter
    directly on the column it narrows.

    `q` is the cross-column keyword (name or skill); `file_name` / `skill_code`
    are the per-column ones. `file_type` is the extension without the dot.
    `verify` ∈ {verified, error, none}. Date params are inclusive `YYYY-MM-DD`.
    """
    tenant = current_tenant()
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    try:
        lo, hi = _day_bounds(date_from, date_to)
        ulo, uhi = _day_bounds(updated_from, updated_to)
    except ValueError as e:
        raise HTTPException(400, "日期参数需为 YYYY-MM-DD 格式") from e
    if verify and verify not in ("verified", "error", "none"):
        raise HTTPException(400, "verify 只能是 verified / error / none")
    sf = session_factory()
    async with sf() as s:
        base = (select(FileRecord, Transaction.skill_code)
                .join(Transaction, FileRecord.transaction_id == Transaction.id)
                .where(FileRecord.tenant_id == tenant))
        # count must carry the same joins/filters or the pager lies
        count_q = (select(func.count()).select_from(FileRecord)
                   .join(Transaction, FileRecord.transaction_id == Transaction.id)
                   .where(FileRecord.tenant_id == tenant))
        conds = []
        if status:
            conds.append(FileRecord.status == status)
        if skill_code:
            conds.append(Transaction.skill_code == skill_code)
        if file_name and file_name.strip():
            conds.append(FileRecord.file_name.ilike(_like(file_name), escape="\\"))
        if file_type and file_type.strip():
            # `type` is derived from the extension at read time, so filtering on
            # it means matching the suffix — there is no column to index
            suffix = file_type.strip().lstrip(".").lower()
            conds.append(FileRecord.file_name.ilike(f"%.{suffix}", escape="\\"))
        if pages_min is not None:
            conds.append(FileRecord.page_count >= pages_min)
        if pages_max is not None:
            conds.append(FileRecord.page_count <= pages_max)
        if verify == "verified":
            conds.append(FileRecord.verified_by.is_not(None))
        elif verify == "error":
            conds.append(and_(FileRecord.error.is_not(None), FileRecord.error != ""))
        elif verify == "none":
            conds.append(and_(FileRecord.verified_by.is_(None),
                              or_(FileRecord.error.is_(None), FileRecord.error == "")))
        if q and q.strip():
            conds.append(or_(FileRecord.file_name.ilike(_like(q), escape="\\"),
                             Transaction.skill_code.ilike(_like(q), escape="\\")))
        if lo is not None:
            conds.append(FileRecord.created_at >= lo)
        if hi is not None:
            conds.append(FileRecord.created_at <= hi)
        if ulo is not None:
            conds.append(FileRecord.updated_at >= ulo)
        if uhi is not None:
            conds.append(FileRecord.updated_at <= uhi)
        for c in conds:
            base = base.where(c)
            count_q = count_q.where(c)
        total = (await s.execute(count_q)).scalar_one()
        rows = (await s.execute(
            base.order_by(FileRecord.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size))).all()
        data = []
        for f, sc in rows:
            size = None
            try:
                size = Path(f.storage_path).stat().st_size
            except OSError:
                pass
            data.append({
                "file_id": f.id, "transaction_id": f.transaction_id,
                "file_name": f.file_name, "skill_code": sc,
                "type": Path(f.file_name).suffix.lstrip(".").upper(),
                "size": size, "page_count": f.page_count, "status": f.status,
                "created_at": f.created_at.isoformat(),
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
                "verified_by": f.verified_by, "error": f.error,
            })
    return {"total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size, "data": data}


@router.get("/stats/home")
async def home_stats():
    """Metric strip (Insavlo home shape): credits, usage, throughput, backlog."""
    tenant = current_tenant()
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min,
                                   tzinfo=timezone.utc)
    sf = session_factory()
    async with sf() as s:
        acct = await s.get(CreditAccount, tenant)
        used = (await s.execute(
            select(func.coalesce(func.sum(CreditLedger.amount), 0.0))
            .where(CreditLedger.tenant_id == tenant,
                   CreditLedger.kind == "shadow_meter"))).scalar_one()

        def _count(*conds):
            return select(func.count()).select_from(FileRecord).where(
                FileRecord.tenant_id == tenant, *conds)

        today = (await s.execute(_count(FileRecord.created_at >= today_start))).scalar_one()
        passed_docs = (await s.execute(_count(FileRecord.status.in_(_DONE)))).scalar_one()
        passed_pages = (await s.execute(
            select(func.coalesce(func.sum(FileRecord.page_count), 0))
            .where(FileRecord.tenant_id == tenant,
                   FileRecord.status.in_(_DONE)))).scalar_one()
        pending = (await s.execute(
            _count(FileRecord.status == "pending_verification"))).scalar_one()
        queued = (await s.execute(_count(FileRecord.status == "queued"))).scalar_one()
        processing = (await s.execute(_count(FileRecord.status == "processing"))).scalar_one()
        today_done = (await s.execute(
            _count(FileRecord.status.in_(_DONE),
                   FileRecord.updated_at >= today_start))).scalar_one()
    return {"remaining_credits": (acct.paid_balance + acct.gift_balance) if acct else 0,
            "used_credits": round(float(used), 2), "today_usage": today,
            "passed_pages": int(passed_pages), "passed_docs": passed_docs,
            "pending_verification": pending, "queued": queued,
            "processing": processing, "today_completed": today_done}


def _flat_value(v) -> str:
    if isinstance(v, dict):
        return str(v.get("$value") or "")
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False)
    return "" if v is None else str(v)


async def _cabinet_rows(skill_code: str, limit: int) -> list[dict]:
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(FileRecord, Transaction.skill_code)
            .join(Transaction, FileRecord.transaction_id == Transaction.id)
            .where(FileRecord.tenant_id == current_tenant(),
                   Transaction.skill_code == skill_code,
                   FileRecord.status.in_(_DONE))
            .order_by(FileRecord.created_at.desc())
            .limit(min(limit, 1000)))).all()
    out = []
    for f, sc in rows:
        flat = {k: _flat_value(v) for k, v in (f.result or {}).items()}
        out.append({"file_id": f.id, "file_name": f.file_name,
                    "status": f.status, "verified_by": f.verified_by,
                    "created_at": f.created_at.isoformat(), **flat})
    return out


@router.get("/stats/usage")
async def usage_stats(days: int = 7, skill_code: str | None = None):
    """Dashboard charts (Insavlo dashboard shape): credits consumed per day and
    per skill, from the shadow-billing ledger."""
    from datetime import timedelta
    tenant = current_tenant()
    days = min(max(days, 1), 366)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(CreditLedger.created_at, CreditLedger.amount, Transaction.skill_code)
            .join(Transaction, CreditLedger.transaction_id == Transaction.id, isouter=True)
            .where(CreditLedger.tenant_id == tenant,
                   CreditLedger.kind == "shadow_meter",
                   CreditLedger.created_at >= since))).all()
    by_day: dict[str, float] = {}
    by_skill: dict[str, float] = {}
    for created, amount, sc in rows:
        if skill_code and sc != skill_code:
            continue
        day = created.date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + float(amount)
        key = sc or "(unknown)"
        by_skill[key] = by_skill.get(key, 0.0) + float(amount)
    return {"days": days,
            "by_day": [{"date": d, "credits": round(v, 2)}
                       for d, v in sorted(by_day.items())],
            "by_skill": sorted(({"skill_code": k, "credits": round(v, 2)}
                                for k, v in by_skill.items()),
                               key=lambda x: -x["credits"])}


@router.get("/cabinet/{skill_code}")
async def cabinet(skill_code: str, limit: int = 200):
    return {"skill_code": skill_code, "rows": await _cabinet_rows(skill_code, limit)}


@router.get("/cabinet/{skill_code}/export.csv", response_class=PlainTextResponse)
async def cabinet_csv(skill_code: str, limit: int = 1000):
    rows = await _cabinet_rows(skill_code, limit)
    if not rows:
        raise HTTPException(404, "no decided files for this skill")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition":
                                      f'attachment; filename="{skill_code}.csv"'})


@router.get("/stats/skills")
async def skill_stats():
    """Per-skill quality metrics (PM item #1). correction rate is the honest
    accuracy proxy: fields humans had to fix / decided files."""
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        # decided + review counts per skill
        rows = (await s.execute(
            select(Transaction.skill_code, FileRecord.status,
                   func.count(FileRecord.id))
            .join(Transaction, FileRecord.transaction_id == Transaction.id)
            .where(FileRecord.tenant_id == tenant)
            .group_by(Transaction.skill_code, FileRecord.status))).all()
        corr = (await s.execute(
            select(Correction.skill_code, Correction.field,
                   func.count(Correction.id))
            .where(Correction.tenant_id == tenant)
            .group_by(Correction.skill_code, Correction.field))).all()

    stats: dict[str, dict] = {}
    for skill, status, n in rows:
        st = stats.setdefault(skill, {"skill_code": skill, "total": 0,
                                      "by_status": {}, "corrections": 0,
                                      "top_corrected_fields": []})
        st["total"] += n
        st["by_status"][status] = st["by_status"].get(status, 0) + n
    for skill, field, n in corr:
        st = stats.setdefault(skill, {"skill_code": skill, "total": 0,
                                      "by_status": {}, "corrections": 0,
                                      "top_corrected_fields": []})
        st["corrections"] += n
        st["top_corrected_fields"].append({"field": field, "count": n})
    for st in stats.values():
        done = sum(st["by_status"].get(k, 0) for k in _DONE)
        reviewed = st["by_status"].get("pending_verification", 0) + \
            sum(1 for _ in ())  # pending now; passed files that went through review
        straight = st["by_status"].get("completed", 0)
        st["decided"] = done
        # straight-through = completed without human loop / all terminal files
        st["straight_through_rate"] = round(straight / done, 3) if done else None
        st["correction_rate"] = round(st["corrections"] / done, 3) if done else None
        st["top_corrected_fields"].sort(key=lambda x: -x["count"])
        st["top_corrected_fields"] = st["top_corrected_fields"][:5]
    return {"skills": sorted(stats.values(), key=lambda x: -x["total"])}
