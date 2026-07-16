"""Verification API (design v0.2 §7 group 3, §5.4): the human-in-the-loop
closing segment. Third parties may embed this whole flow (API-first).

Concepts kept distinct (PM item #2): assignee = who SHOULD review;
locked_by = who IS reviewing right now (TTL lock, auto-expires — HA §2.6).
Every field edit writes a Correction row — the accuracy-proxy data feeding the
skill quality dashboard (PM item #1). M1 identity: X-User header (dev mode);
TODO(M1.5): real user id from JWT once auth lands.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, Correction, FileRecord, Transaction
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1/review", tags=["review"])

LOCK_TTL = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_expired(f: FileRecord) -> bool:
    if f.locked_at is None:
        return True
    locked_at = f.locked_at if f.locked_at.tzinfo else f.locked_at.replace(tzinfo=timezone.utc)
    return _now() - locked_at > LOCK_TTL


async def _get_file(s, file_id: str) -> FileRecord:
    f = await s.get(FileRecord, file_id)
    if f is None or f.tenant_id != current_tenant():   # cross-tenant probe -> 404 (§11.2)
        raise HTTPException(404, "file not found")
    return f


@router.get("/queue")
async def queue(skill_code: str | None = None, assignee: str | None = None,
                limit: int = 50):
    """Pending-verification worklist, oldest first (review by age)."""
    sf = session_factory()
    async with sf() as s:
        q = (select(FileRecord, Transaction.skill_code)
             .join(Transaction, FileRecord.transaction_id == Transaction.id)
             .where(FileRecord.tenant_id == current_tenant(),
                    FileRecord.status == "pending_verification")
             .order_by(FileRecord.created_at)
             .limit(min(limit, 200)))
        if skill_code:
            q = q.where(Transaction.skill_code == skill_code)
        if assignee:
            q = q.where(FileRecord.assignee == assignee)
        rows = (await s.execute(q)).all()
        return [{
            "file_id": f.id, "file_name": f.file_name, "skill_code": sc,
            "transaction_id": f.transaction_id, "page_count": f.page_count,
            "assignee": f.assignee,
            "locked_by": None if _lock_expired(f) else f.locked_by,
            "created_at": f.created_at.isoformat(),
        } for f, sc in rows]


@router.get("/{file_id}")
async def detail(file_id: str):
    """Everything the dual-screen UI needs: result fields + page dimensions
    (bbox overlay coordinate base — bboxes live in page pixel space)."""
    import json
    from pathlib import Path

    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        pages = []
        if f.udr_path and Path(f.udr_path).exists():
            udr = json.loads(Path(f.udr_path).read_text(encoding="utf-8"))
            pages = [{"page_no": p["page_no"], "width": p.get("width", 0),
                      "height": p.get("height", 0)} for p in udr.get("pages", [])]
        return {
            "file_id": f.id, "file_name": f.file_name, "status": f.status,
            "transaction_id": f.transaction_id, "page_count": f.page_count,
            "result": f.result, "assignee": f.assignee,
            "locked_by": None if _lock_expired(f) else f.locked_by,
            "verified_by": f.verified_by, "pages": pages,
        }


class AssignBody(BaseModel):
    assignee: str


@router.post("/{file_id}/assign")
async def assign(file_id: str, body: AssignBody,
                 x_user: str = Header(default="anonymous")):
    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        f.assignee = body.assignee
        s.add(AuditLog(tenant_id=f.tenant_id, actor=x_user, action="review.assign",
                       detail={"file_id": file_id, "assignee": body.assignee}))
        await s.commit()
    return {"file_id": file_id, "assignee": body.assignee}


@router.post("/{file_id}/lock")
async def lock(file_id: str, x_user: str = Header(default="anonymous")):
    """Acquire the review lock. 409 if actively held by someone else;
    expired locks are silently reclaimed (reviewer went offline, §2.6)."""
    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        if f.status != "pending_verification":
            raise HTTPException(400, f"file not reviewable, status={f.status}")
        if f.locked_by and f.locked_by != x_user and not _lock_expired(f):
            raise HTTPException(409, f"locked by {f.locked_by}")
        f.locked_by = x_user
        f.locked_at = _now()
        await s.commit()
    return {"file_id": file_id, "locked_by": x_user, "ttl_minutes": 15}


@router.post("/{file_id}/unlock")
async def unlock(file_id: str, x_user: str = Header(default="anonymous")):
    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        if f.locked_by == x_user:
            f.locked_by = None
            f.locked_at = None
            await s.commit()
    return {"file_id": file_id, "locked_by": None}


class FieldEdit(BaseModel):
    field: str
    value: str


class FieldsPatch(BaseModel):
    edits: list[FieldEdit]


@router.patch("/{file_id}/fields")
async def patch_fields(file_id: str, body: FieldsPatch,
                       x_user: str = Header(default="anonymous")):
    """Apply human corrections. Business rules: edit requires holding the lock;
    each change writes a Correction row (old->new, reviewer, skill version);
    a human-set value becomes confidence 3 and is marked $corrected."""
    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        if f.locked_by != x_user or _lock_expired(f):
            raise HTTPException(423, "acquire the lock before editing")
        txn = await s.get(Transaction, f.transaction_id)
        result = dict(f.result or {})
        applied = []
        for e in body.edits:
            cell = result.get(e.field)
            if not isinstance(cell, dict):
                raise HTTPException(400, f"unknown or non-editable field: {e.field}")
            old = str(cell.get("$value") or "")
            if old == e.value:
                continue
            s.add(Correction(
                tenant_id=f.tenant_id, file_id=f.id,
                skill_code=txn.skill_code if txn else "",
                skill_version=txn.skill_version if txn else 0,
                field=e.field, old_value=old, new_value=e.value, reviewer=x_user))
            cell = dict(cell)
            cell["$value"] = e.value
            cell["$confidence"] = 3          # human truth
            cell["$corrected"] = True
            result[e.field] = cell
            applied.append(e.field)
        f.result = result
        await s.commit()
    return {"file_id": file_id, "corrected_fields": applied}


class DecisionBody(BaseModel):
    comment: str = ""


@router.post("/{file_id}/confirm")
async def confirm(file_id: str, body: DecisionBody | None = None,
                  x_user: str = Header(default="anonymous")):
    return await _decide(file_id, "passed", x_user, body.comment if body else "")


@router.post("/{file_id}/reject")
async def reject(file_id: str, body: DecisionBody | None = None,
                 x_user: str = Header(default="anonymous")):
    return await _decide(file_id, "rejected", x_user, body.comment if body else "")


async def _decide(file_id: str, new_status: str, actor: str, comment: str) -> dict:
    sf = session_factory()
    async with sf() as s:
        f = await _get_file(s, file_id)
        if f.status != "pending_verification":
            raise HTTPException(400, f"file not reviewable, status={f.status}")
        f.status = new_status
        f.verified_by = actor
        f.verified_at = _now()
        f.locked_by = None
        f.locked_at = None
        s.add(AuditLog(tenant_id=f.tenant_id, actor=actor, action=f"review.{new_status}",
                       detail={"file_id": file_id, "comment": comment}))
        # transaction rolls up: all files decided -> completed
        siblings = (await s.execute(
            select(FileRecord.status)
            .where(FileRecord.transaction_id == f.transaction_id))).scalars().all()
        undecided = {"queued", "processing", "pending_verification"}
        if not (set(siblings) - {new_status}) & undecided:
            txn = await s.get(Transaction, f.transaction_id)
            if txn:
                txn.status = "completed"
        tenant = f.tenant_id
        await s.commit()
    from app.integrations import webhooks
    await webhooks.fire(tenant, f"file.{new_status}",
                        {"file_id": file_id, "status": new_status, "by": actor})
    return {"file_id": file_id, "status": new_status, "verified_by": actor}
