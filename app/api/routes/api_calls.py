"""Metadata only: no document contents, filenames, request bodies or credentials."""
from datetime import date
from fastapi import APIRouter, Query
from sqlalchemy import func, select
from app.api.routes.data import _day_bounds, _utc
from app.db import session_factory
from app.models import ApiCallLog
from app.tenancy import current_actor, current_tenant

router = APIRouter(prefix="/api/v1", tags=["api-calls"])


@router.get("/api-calls")
async def api_calls(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100),
                    key_id: str | None = None, endpoint: str | None = None,
                    date_from: date | None = None, date_to: date | None = None):
    actor = current_actor()
    cond = [ApiCallLog.tenant_id == current_tenant()]
    if actor.get("api_key_id"):
        cond.append(ApiCallLog.api_key_id == actor["api_key_id"])
    elif actor.get("role") != "admin":
        cond.append(ApiCallLog.owner_user_id == (actor.get("user_id") or ""))
    if key_id:
        cond.append(ApiCallLog.api_key_id == key_id)
    if endpoint:
        cond.append(ApiCallLog.endpoint == endpoint)
    lo, hi = _day_bounds(str(date_from) if date_from else None, str(date_to) if date_to else None)
    if lo:
        cond.append(ApiCallLog.created_at >= lo)
    if hi:
        cond.append(ApiCallLog.created_at <= hi)
    async with session_factory()() as s:
        total = (await s.execute(select(func.count()).select_from(ApiCallLog).where(*cond))).scalar_one()
        rows = (await s.execute(select(ApiCallLog).where(*cond)
                .order_by(ApiCallLog.created_at.desc(), ApiCallLog.id.desc())
                .offset((page - 1) * limit).limit(limit))).scalars().all()
    return {"total": total, "page": page, "limit": limit, "data": [
        {"id": r.id, "key_id": r.api_key_id, "key_name": r.key_name,
         "endpoint": r.endpoint, "status_code": r.status_code,
         "duration_ms": r.duration_ms, "page_count": r.page_count,
         "size_bytes": r.size_bytes, "created_at": _utc(r.created_at).isoformat()}
        for r in rows]}
