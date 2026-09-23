"""Data & analytics API (design v0.2 §7 groups 4-5):
- Home: metric strip + the full task ledger (Insavlo home shape)
- Cabinet: structured results of decided files, per skill, JSON or CSV export
- Stats: skill quality metrics from day-one data (fill rate / correction rate /
  straight-through rate — PM item #1, the "how accurate is it" answer)
"""
import csv
import io
import json
import logging
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, delete, func, or_, select

from app.audit import human_event
from app.api.task_groups import summary as task_summary
from app.db import session_factory
from app.models import (AuditLog, Correction, CreditAccount, CreditLedger,
                        FileArtifact, FileRecord, Skill, Transaction)
from app.storage import get_storage
from app.tenancy import current_actor, current_tenant, require_role

from app.visibility import visible_file_cond, visible_txn_cond

router = APIRouter(prefix="/api/v1", tags=["data"])

log = logging.getLogger(__name__)

_DONE = ("passed", "completed", "exported")

# Admin task deletion leaves the transaction row behind as a tombstone so the
# append-only money trail keeps its anchor; every task-facing read must skip it.
_NOT_DELETED = Transaction.status != "deleted"


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


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _source_cond(source: str | None):
    if source == "api":
        return Transaction.initiator_type == "api_key"
    if source == "manual":
        return or_(Transaction.initiator_type.is_(None), Transaction.initiator_type != "api_key")
    return True


def _initiator_cond(value: str):
    """SQL for the 发起人 column filter (2026-09-19 需求).

    Value shapes mirror the options endpoint: `user:<label>` / `api_key:<label>`
    match one initiator, `unknown` matches rows that predate the snapshot,
    `anonymous` matches免登录 submissions (auth-off deployments and集成调用).
    """
    value = value.strip()
    if value == "unknown":
        return or_(Transaction.initiator_type.is_(None),
                   Transaction.initiator_type == "unknown")
    if ":" in value:
        itype, label = value.split(":", 1)
        if itype in ("user", "api_key"):
            return and_(Transaction.initiator_type == itype,
                        Transaction.initiator_label == label)
    # plain type name (anonymous / user / api_key): label-independent
    return Transaction.initiator_type == value


@router.get("/files")
async def list_files(source: Literal["manual", "api"] | None = None, status: str | None = None, q: str | None = None,
                     skill_code: str | None = None,
                     file_name: str | None = None, file_type: str | None = None,
                     initiator: str | None = None,
                     pages_min: int | None = None, pages_max: int | None = None,
                     verify: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     updated_from: str | None = None, updated_to: str | None = None,
                     page: int = 1, page_size: int = 20):
    """Task ledger for the Home page: every file, newest first, paged — the
    Insavlo home-table shape.

    Narrowing (P09, 2026-08-26) is server-side on purpose: the console pages at
    20 rows, so filtering in the browser would only ever search the page in hand
    and report a total that disagrees with what is on screen. Static root-file
    fields narrow in SQL; grouped child status/verification narrows after the
    server has calculated the root task, before total and pagination.

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
        # 9.15 R20: left-join the skill identity so the ledger can show its
        # display name (fallback = code when the skill row is gone). Scoped to
        # this tenant — Skill.code is a global PK, tenant_id is a plain column.
        base = (select(FileRecord, Transaction.skill_code, Skill.name,
                       Transaction.initiator_type, Transaction.initiator_label)
                .join(Transaction, FileRecord.transaction_id == Transaction.id)
                .outerjoin(Skill, and_(Skill.code == Transaction.skill_code,
                                       Skill.tenant_id == FileRecord.tenant_id))
                .where(visible_file_cond(), FileRecord.transaction_id.in_(select(Transaction.id).where(_source_cond(source))), FileRecord.tenant_id == tenant,
                       FileRecord.parent_file_id.is_(None),
                       # 9.15 WP5 (§3.9): Playground test runs stay out of the ledger
                       Transaction.purpose != "test",
                       _NOT_DELETED))
        conds = []
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
        if initiator and initiator.strip():
            conds.append(_initiator_cond(initiator))
        if q and q.strip():
            conds.append(or_(FileRecord.file_name.ilike(_like(q), escape="\\"),
                             Transaction.skill_code.ilike(_like(q), escape="\\"),
                             Skill.name.ilike(_like(q), escape="\\")))
        if lo is not None:
            conds.append(FileRecord.created_at >= lo)
        if hi is not None:
            conds.append(FileRecord.created_at <= hi)
        for c in conds:
            base = base.where(c)
        rows = (await s.execute(base.order_by(FileRecord.created_at.desc()))).all()
        root_ids = [f.id for f, _, _, _, _ in rows]
        children_by_parent: dict[str, list[FileRecord]] = {}
        if root_ids:
            children = (await s.execute(
                select(FileRecord)
                .where(visible_file_cond(), FileRecord.transaction_id.in_(select(Transaction.id).where(_source_cond(source))), FileRecord.tenant_id == tenant,
                       FileRecord.parent_file_id.in_(root_ids))
                .order_by(FileRecord.created_at, FileRecord.id))).scalars().all()
            for child in children:
                children_by_parent.setdefault(child.parent_file_id, []).append(child)

        all_data = []
        for f, sc, sname, itype, ilabel in rows:
            children = children_by_parent.get(f.id, [])
            meta = task_summary(f, children)
            if status and meta["status"] != status:
                continue
            updated_at = _utc(meta["updated_at"])
            if ulo is not None and updated_at < ulo:
                continue
            if uhi is not None and updated_at > uhi:
                continue
            any_verified = bool(f.verified_by or any(c.verified_by for c in children))
            if verify == "verified" and not meta["verified_by"]:
                continue
            if verify == "error" and not meta["error"]:
                continue
            if verify == "none" and (any_verified or meta["error"]):
                continue
            size = None
            try:
                size = get_storage().size(f.storage_path)
            except OSError:
                pass
            processed_at = meta["processed_at"]
            processing_seconds = None
            if processed_at is not None:
                start = _utc(f.created_at) if f.created_at else None
                end = _utc(processed_at)
                if start is not None:
                    processing_seconds = round((end - start).total_seconds(), 1)
            all_data.append({
                "file_id": f.id, "transaction_id": f.transaction_id,
                "file_name": f.file_name, "skill_code": sc,
                "skill_name": sname or sc,
                "initiator_type": itype or "unknown",
                # legacy rows (NULL/unknown after the WP2 migration backfill)
                # carry no label: the UI renders "—" for them
                "initiator_label": ilabel if itype and itype != "unknown" else None,
                "type": Path(f.file_name).suffix.lstrip(".").upper(),
                "size": size, "page_count": f.page_count, "status": meta["status"],
                "created_at": f.created_at.isoformat(),
                "updated_at": meta["updated_at"].isoformat(),
                "processed_at": processed_at.isoformat() if processed_at else None,
                "processing_seconds": processing_seconds,
                "verified_by": meta["verified_by"], "error": meta["error"],
                "child_count": meta["child_count"],
                "pending_children": meta["pending_children"],
                "status_counts": meta["status_counts"],
            })
        total = len(all_data)
        start = (page - 1) * page_size
        data = all_data[start:start + page_size]
    return {"total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size, "data": data}


@router.get("/files/initiators")
async def list_initiators(source: Literal["manual", "api"] | None = None):
    """Distinct 发起人 values of this tenant's task ledger, with row counts.

    The column filter needs the whole vocabulary, not just the page in hand;
    options come from the same population the ledger shows (root files,
    production purpose, not deleted), so a choice can never yield zero rows.
    `value` is what /files?initiator= expects.
    """
    tenant = current_tenant()
    sf = session_factory()
    # NULL (pre-snapshot rows) and the literal "unknown" are ONE bucket, so they
    # must be coalesced in SQL — grouping them separately produced two identical
    # 「历史任务」options (caught by tests/test_task_filters.py).
    itype_col = func.coalesce(Transaction.initiator_type, "unknown")
    async with sf() as s:
        rows = (await s.execute(
            select(itype_col, Transaction.initiator_label,
                   func.count(FileRecord.id))
            .join(Transaction, FileRecord.transaction_id == Transaction.id)
            .where(visible_file_cond(), FileRecord.transaction_id.in_(select(Transaction.id).where(_source_cond(source))), FileRecord.tenant_id == tenant,
                   FileRecord.parent_file_id.is_(None),
                   Transaction.purpose != "test",
                   _NOT_DELETED)
            .group_by(itype_col, Transaction.initiator_label))).all()

    merged: dict[str, dict] = {}
    for itype, label, count in rows:
        if itype in ("user", "api_key") and label:
            value, out_label = f"{itype}:{label}", label
        else:
            # legacy rows (no snapshot) and免登录/anonymous submissions are a
            # single bucket each — "历史任务" matches R21's ledger wording
            value = itype or "unknown"
            out_label = ("历史任务" if value == "unknown"
                         else ("免登录" if value == "anonymous" else value))
        opt = merged.setdefault(value, {"value": value, "label": out_label,
                                        "type": value.split(":", 1)[0]
                                                if ":" in value else value,
                                        "count": 0})
        opt["count"] += count
    out = list(merged.values())
    order = {"unknown": 0, "user": 1, "api_key": 2, "anonymous": 3}
    out.sort(key=lambda x: (order.get(x["type"], 9), -x["count"], x["label"]))
    return {"initiators": out}


@router.delete("/files/{file_id}")
async def delete_task(file_id: str, _: None = Depends(require_role("admin"))):
    """Admin-only hard delete of one task (one uploaded root file).

    Removes the file rows — the root and every child document of a split — the
    stored blobs (original, split slice, UDR, page images, preview cache) and
    the generated output files, so the task disappears from the ledger, the
    cabinet, the review queue, the dashboard counts and every download route.

    The transaction row is kept as a tombstone (status "deleted", files and
    purpose untouched) once its LAST root file is gone, because the append-only
    credit ledger points at it; `_NOT_DELETED` hides it everywhere the task
    surfaces. The audit log records file name / skill / page count so the
    deletion stays reconstructible.

    Deletion is allowed while the task is still queued/processing: the runner
    re-checks this tombstone after every stage and stops persisting results
    (runner._txn_deleted), so an in-flight task cannot come back to life.
    """
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        root = await s.get(FileRecord, file_id)
        if root is None or root.tenant_id != tenant:   # cross-tenant probe -> 404
            raise HTTPException(404, "file not found")
        if root.parent_file_id is not None:
            raise HTTPException(400, "这是一份拆分出来的子单据，请删除它所属的任务")
        txn = await s.get(Transaction, root.transaction_id)
        if txn is not None and txn.status == "deleted":
            raise HTTPException(404, "file not found")   # already deleted

        # one task = the root plus its child documents (children are internal
        # jobs, never separate ledger rows — they share the root's original)
        ids = [root.id]
        ids += list((await s.execute(
            select(FileRecord.id)
            .where(visible_file_cond(), FileRecord.tenant_id == tenant,
                   FileRecord.parent_file_id == root.id))).scalars().all())
        rows = (await s.execute(
            select(FileRecord).where(FileRecord.id.in_(ids)))).scalars().all()
        # dedupe: a split child points at its own "split/<id>.pdf" but falls
        # back to the parent's original when the physical slice failed
        keys = {r.storage_path for r in rows if r.storage_path}
        keys |= {r.udr_path for r in rows if r.udr_path}
        keys |= {r.images_path for r in rows if r.images_path}
        keys |= {f"preview/{r.id}.pdf" for r in rows}
        artifacts = (await s.execute(
            select(FileArtifact)
            .where(FileArtifact.tenant_id == tenant,
                   FileArtifact.file_id.in_(ids)))).scalars().all()
        keys |= {a.storage_key for a in artifacts if a.storage_key}

        detail = {"file_id": root.id, "file_name": root.file_name,
                  "skill_code": txn.skill_code if txn else None,
                  "page_count": root.page_count, "status": root.status,
                  "child_count": len(ids) - 1, "transaction_id": root.transaction_id}
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="files.deleted", detail=detail))
        # corrections keep no foreign key (plain file_id column): drop them
        # explicitly — their file is gone, and the accuracy proxy reads them
        # per skill, where a dangling row would count forever
        await s.execute(delete(Correction).where(Correction.tenant_id == tenant,
                                                 Correction.file_id.in_(ids)))
        await s.execute(delete(FileArtifact).where(FileArtifact.tenant_id == tenant,
                                                  FileArtifact.file_id.in_(ids)))
        await s.execute(delete(FileRecord).where(visible_file_cond(), FileRecord.tenant_id == tenant,
                                                 FileRecord.id.in_(ids)))
        if txn is not None and not (await s.execute(
                select(FileRecord.id)
                .where(FileRecord.transaction_id == root.transaction_id,
                       FileRecord.parent_file_id.is_(None))
                .limit(1))).first():
            # one upload may carry several root files (each its own ledger row):
            # the tombstone is set only once the LAST one is gone, so deleting
            # one task can never hide its siblings from the ledger
            txn.status = "deleted"
        await s.commit()

    # blobs go last, after the rows are committed: a storage failure leaves an
    # orphaned file (harmless, collectable), never a row pointing at nothing
    st = get_storage()
    removed = 0
    for key in sorted(keys):
        try:
            removed += 1 if st.delete(key) else 0
        except OSError as e:                       # noqa: PERF203
            log.warning("task delete: blob %s not removed: %s", key, e)
    return {"file_id": root.id, "transaction_id": detail["transaction_id"],
            "deleted_children": detail["child_count"], "deleted_blobs": removed}


@router.get("/stats/home")
async def home_stats(source: Literal["manual", "api"] | None = None):
    """Metric strip (Insavlo home shape): credits, usage, throughput, backlog."""
    tenant = current_tenant()
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min,
                                   tzinfo=timezone.utc)
    sf = session_factory()
    async with sf() as s:
        acct = await s.get(CreditAccount, tenant)
        used = (await s.execute(
            select(func.coalesce(func.sum(CreditLedger.amount), 0.0))
            .where(CreditLedger.transaction_id.in_(select(Transaction.id).where(visible_txn_cond(), _source_cond(source))),
                   CreditLedger.tenant_id == tenant,
                   CreditLedger.kind == "shadow_meter"))).scalar_one()

        # §3.9: Playground test runs stay out of the home metrics
        rows = (await s.execute(
            select(FileRecord, Transaction.purpose)
            .join(Transaction, FileRecord.transaction_id == Transaction.id)
            .where(visible_file_cond(), FileRecord.transaction_id.in_(select(Transaction.id).where(_source_cond(source))), FileRecord.tenant_id == tenant, _NOT_DELETED))).all()
        records = [f for f, purpose in rows if purpose != "test"]
        roots = [f for f in records if f.parent_file_id is None]
        children_by_parent: dict[str, list[FileRecord]] = {}
        for child in records:
            if child.parent_file_id:
                children_by_parent.setdefault(child.parent_file_id, []).append(child)
        grouped = [(root, task_summary(root, children_by_parent.get(root.id, [])))
                   for root in roots]

        today = sum(1 for root, _ in grouped if _utc(root.created_at) >= today_start)
        passed_docs = sum(1 for _, meta in grouped if meta["status"] in _DONE)
        passed_pages = (await s.execute(
            select(func.coalesce(func.sum(FileRecord.page_count), 0))
            .where(visible_file_cond(), FileRecord.transaction_id.in_(select(Transaction.id).where(_source_cond(source))), FileRecord.tenant_id == tenant,
                   FileRecord.status.in_(_DONE)))).scalar_one()
        pending = sum(1 for _, meta in grouped if meta["status"] == "pending_verification")
        queued = sum(1 for _, meta in grouped if meta["status"] == "queued")
        processing = sum(1 for _, meta in grouped if meta["status"] == "processing")
        today_done = sum(1 for _, meta in grouped
                         if meta["status"] in _DONE
                         and _utc(meta["updated_at"]) >= today_start)
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
            .where(visible_file_cond(), FileRecord.tenant_id == current_tenant(),
                   Transaction.skill_code == skill_code,
                   Transaction.purpose != "test",   # §3.9
                   _NOT_DELETED,
                   FileRecord.status.in_(_DONE))
            .order_by(FileRecord.created_at.desc())
            .limit(min(limit, 1000)))).all()
    out = []
    for f, sc in rows:
        flat = {k: _flat_value(v) for k, v in (f.result or {}).items()}
        meta = f.document_meta or {}
        # 9.15 WP4: advanced skills emit one row per child document; doc_type
        # is a new column, legacy rows just leave it out
        row = {"file_id": f.id, "file_name": f.file_name,
               "status": f.status, "verified_by": f.verified_by,
               "created_at": f.created_at.isoformat()}
        if meta.get("doc_index") is not None:
            row["doc_type"] = meta.get("doc_type") or ""
            row["source_pages"] = meta.get("source_pages") or []
        out.append({**row, **flat})
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
            .where(CreditLedger.transaction_id.in_(select(Transaction.id).where(visible_txn_cond())),
                   CreditLedger.tenant_id == tenant,
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
    async with session_factory()() as s:
        human_event(s, "cabinet.exported", {"skill_code": skill_code, "rows": len(rows)})
        await s.commit()
    buf = io.StringIO()
    # union of keys across rows: doc_type/source_pages exist only on advanced
    # children — the column set must not depend on which row came first
    fieldnames: list[str] = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    from app.api.http_headers import content_disposition
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition":
                                      content_disposition(f"{skill_code}.csv")})


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
            .where(visible_file_cond(), FileRecord.tenant_id == tenant,
                   Transaction.purpose != "test",   # §3.9
                   _NOT_DELETED)
            .group_by(Transaction.skill_code, FileRecord.status))).all()
        corr = (await s.execute(
            select(Correction.skill_code, Correction.field,
                   func.count(Correction.id))
            .where(Correction.tenant_id == tenant, Correction.file_id.in_(
                select(FileRecord.id).where(visible_file_cond())))
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
        straight = st["by_status"].get("completed", 0)
        st["decided"] = done
        # straight-through = completed without human loop / all terminal files
        st["straight_through_rate"] = round(straight / done, 3) if done else None
        st["correction_rate"] = round(st["corrections"] / done, 3) if done else None
        st["top_corrected_fields"].sort(key=lambda x: -x["count"])
        st["top_corrected_fields"] = st["top_corrected_fields"][:5]
    return {"skills": sorted(stats.values(), key=lambda x: -x["total"])}
