"""Operation-log reader (2026-09-09 需求: 管理员可看操作日志).

audit_log is append-only (§9.1); writers live next to the actions they record
(auth / settings / review / billing / skills). This router is the admin view
over them — tenant-scoped like every read, admin-only like user management.
"""
from datetime import date
from app.api.routes.data import _day_bounds, _utc
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.db import session_factory
from app.models import AuditLog
from app.tenancy import current_tenant, has_role

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def _escape_like(text: str) -> str:
    """User input inside LIKE: backslash/percent/underscore lose their magic."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/logs")
async def audit_logs(
    q: str = Query(default="", max_length=100, description="按操作人或动作模糊搜索"),
    action: str = Query(default="", max_length=100,
                        description="动作前缀过滤，如 skills 匹配 skills.*"),
    actor: str = Query(default="", max_length=320),
    date_from: date | None = None, date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Admin-only, newest first. `q` searches actor/action text; `action`
    narrows to a family (auth / skills / review / settings / billing)."""
    if not has_role("admin"):
        raise HTTPException(403, "admin role required")
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        conds = [AuditLog.tenant_id == tenant]
        if actor:
            conds.append(AuditLog.actor == actor)
        lo, hi = _day_bounds(str(date_from) if date_from else None, str(date_to) if date_to else None)
        if lo:
            conds.append(AuditLog.created_at >= lo)
        if hi:
            conds.append(AuditLog.created_at <= hi)
        if q:
            pat = f"%{_escape_like(q.strip())}%"
            conds.append(or_(AuditLog.actor.like(pat, escape="\\"),
                             AuditLog.action.like(pat, escape="\\")))
        if action:
            conds.append(AuditLog.action.like(f"{_escape_like(action)}%",
                                              escape="\\"))
        total = (await s.execute(
            select(func.count()).select_from(AuditLog).where(*conds))).scalar_one()
        rows = (await s.execute(
            select(AuditLog).where(*conds)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * limit).limit(limit))).scalars().all()
        return {"total": total, "page": page, "limit": limit,
                "data": [{"id": r.id, "actor": r.actor, "action": r.action,
                          "detail": r.detail,
                          "created_at": _utc(r.created_at).isoformat()} for r in rows]}


@router.get("/actors")
async def audit_actors():
    if not has_role("admin"):
        raise HTTPException(403, "admin role required")
    async with session_factory()() as s:
        actors = (await s.execute(select(AuditLog.actor).where(
            AuditLog.tenant_id == current_tenant()).distinct().order_by(AuditLog.actor))).scalars().all()
    return {"actors": list(actors)}
