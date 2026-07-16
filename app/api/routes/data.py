"""Data & analytics API (design v0.2 §7 groups 4-5):
- Cabinet: structured results of decided files, per skill, JSON or CSV export
- Stats: skill quality metrics from day-one data (fill rate / correction rate /
  straight-through rate — PM item #1, the "how accurate is it" answer)
"""
import csv
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.db import session_factory
from app.models import Correction, FileRecord, Transaction
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1", tags=["data"])

_DONE = ("passed", "completed", "exported")


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
