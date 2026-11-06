"""Verification API (design v0.2 §7 group 3, §5.4): the human-in-the-loop
closing segment. Third parties may embed this whole flow (API-first).

Concepts kept distinct (PM item #2): assignee = who SHOULD review;
locked_by = who IS reviewing right now (TTL lock, auto-expires — HA §2.6).
Every field edit writes a Correction row — the accuracy-proxy data feeding the
skill quality dashboard (PM item #1). Identity: the JWT actor when auth is on
(headers can't impersonate past the credential); X-User header in dev mode.
"""
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.audit import human_event
from app.api.task_groups import summary as task_summary
from app.config import get_settings
from app.db import session_factory
from app.models import AuditLog, Correction, FileRecord, Transaction
from app.result_view import output_shape_of
from app.storage import get_storage
from app.tenancy import current_actor, current_tenant, require_role

from app.visibility import require_file, reviewable_file_cond, visible_file_cond

router = APIRouter(prefix="/api/v1/review", tags=["review"])

LOCK_TTL = timedelta(minutes=15)


def _identity(x_user: str) -> str:
    """Reviewer identity for locks/corrections/audit."""
    if get_settings().auth_mode == "on":
        return current_actor()["name"]
    return x_user


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_expired(f: FileRecord) -> bool:
    if f.locked_at is None:
        return True
    locked_at = f.locked_at if f.locked_at.tzinfo else f.locked_at.replace(tzinfo=timezone.utc)
    return _now() - locked_at > LOCK_TTL


def _pages(f: FileRecord) -> list[dict]:
    # udr_path holds a storage key (udr/<id>.json) — or a legacy absolute path,
    # which LocalStorage passes through. Read via the storage seam (D1), never
    # via bare Path(): a relative key would resolve against the CWD.
    st = get_storage()
    if not f.udr_path or not st.exists(f.udr_path):
        return []
    udr = json.loads(st.read_bytes(f.udr_path))
    return [{"page_no": p["page_no"], "width": p.get("width", 0),
             "height": p.get("height", 0)} for p in udr.get("pages", [])]


def _detail_payload(f: FileRecord, snapshot_pkg: dict | None = None) -> dict:
    meta = f.document_meta or {}
    schema = meta.get("effective_schema") or snapshot_pkg or {}
    return {
        "output_shape": output_shape_of(f, snapshot_pkg),
        "review_reasons": meta.get("review_reasons") or [],
        **({"table_headers": meta["table_headers"]} if meta.get("table_headers") else {}),
        "result_revision": f.result_revision or 0,
        "table_columns": {field["name"]: [column["name"] for column in field.get("columns", [])]
                          for field in schema.get("fields", []) if field.get("type") == "table"},
        "file_id": f.id, "file_name": f.file_name, "status": f.status,
        "transaction_id": f.transaction_id, "page_count": f.page_count,
        "parent_file_id": f.parent_file_id, "result": f.result or {},
        "assignee": f.assignee,
        "locked_by": None if _lock_expired(f) else f.locked_by,
        "verified_by": f.verified_by, "pages": _pages(f),
    }


@router.get("/queue")
async def queue(skill_code: str | None = None, assignee: str | None = None,
                limit: int = 50):
    """Pending root-file worklist, oldest first (review by age)."""
    sf = session_factory()
    async with sf() as s:
        q = (select(FileRecord, Transaction.skill_code)
             .join(Transaction, FileRecord.transaction_id == Transaction.id)
             .where(visible_file_cond(), FileRecord.tenant_id == current_tenant(),
                    FileRecord.status == "pending_verification",
                    Transaction.purpose != "test")   # 9.15 WP5 (§3.9)
             .order_by(FileRecord.created_at))
        if skill_code:
            q = q.where(Transaction.skill_code == skill_code)
        if assignee:
            q = q.where(FileRecord.assignee == assignee)
        rows = (await s.execute(q)).all()
        pending_by_root: dict[str, list[FileRecord]] = {}
        skill_by_root: dict[str, str] = {}
        order: list[str] = []
        for f, sc in rows:
            root_id = f.parent_file_id or f.id
            if root_id not in pending_by_root:
                order.append(root_id)
            pending_by_root.setdefault(root_id, []).append(f)
            skill_by_root[root_id] = sc
        if not order:
            return []
        roots = (await s.execute(
            select(FileRecord)
            .where(visible_file_cond(), FileRecord.tenant_id == current_tenant(),
                   FileRecord.id.in_(order)))).scalars().all()
        roots_by_id = {f.id: f for f in roots}
        all_children = (await s.execute(
            select(FileRecord)
            .where(visible_file_cond(), FileRecord.tenant_id == current_tenant(),
                   FileRecord.parent_file_id.in_(order)))).scalars().all()
        child_count: dict[str, int] = {}
        for child in all_children:
            child_count[child.parent_file_id] = child_count.get(child.parent_file_id, 0) + 1

        out = []
        for root_id in order[:min(limit, 200)]:
            root = roots_by_id.get(root_id)
            if root is None:
                continue
            pending = pending_by_root[root_id]
            active_lock = next((f.locked_by for f in pending
                                if f.locked_by and not _lock_expired(f)), None)
            out.append({
                "file_id": root.id, "file_name": root.file_name,
                "skill_code": skill_by_root[root_id],
                "transaction_id": root.transaction_id, "page_count": root.page_count,
                "assignee": pending[0].assignee, "locked_by": active_lock,
                "created_at": root.created_at.isoformat(),
                "child_count": child_count.get(root.id, 0),
                "pending_children": len(pending) if child_count.get(root.id, 0) else 0,
            })
        return out


@router.get("/{file_id}")
async def detail(file_id: str):
    """Everything the dual-screen UI needs: result fields + page dimensions
    (bbox overlay coordinate base — bboxes live in page pixel space)."""
    sf = session_factory()
    async with sf() as s:
        requested = await require_file(s, file_id)
        root = requested if requested.parent_file_id is None \
            else await require_file(s, requested.parent_file_id)
        children = (await s.execute(
            select(FileRecord)
            .where(visible_file_cond(), FileRecord.tenant_id == current_tenant(),
                   FileRecord.parent_file_id == root.id)
            .order_by(FileRecord.created_at, FileRecord.id))).scalars().all()
        file_ids = [root.id] + [child.id for child in children]
        reviewable_ids = set((await s.scalars(select(FileRecord.id).where(
            FileRecord.id.in_(file_ids), reviewable_file_cond()))).all())
        txn = await s.get(Transaction, root.transaction_id)
        snapshot = (txn.execution_snapshot or {}).get("package") if txn else None
        if snapshot is None and txn:
            from app.models import SkillVersion
            version = (await s.execute(select(SkillVersion).where(
                SkillVersion.skill_code == txn.skill_code,
                SkillVersion.version == txn.skill_version))).scalar_one_or_none()
            snapshot = version.package if version else None
        payload = _detail_payload(root, snapshot)
        payload["can_review"] = root.id in reviewable_ids
        payload["children"] = []
        payload["child_count"] = len(children)
        payload["pending_children"] = 0
        payload["status_counts"] = {}
        payload["active_file_id"] = root.id
        if not children:
            return payload

        from app.api.routes.docmeta import document_view
        offset = 0
        child_payloads = []
        for child in children:
            item = _detail_payload(child, snapshot)
            item["can_review"] = child.id in reviewable_ids
            item["page_offset"] = offset
            # 9.15 WP4 (§3.6): classification metadata for advanced children —
            # page mapping prefers source_pages; legacy children keep the
            # cumulative page_offset behaviour
            item["document"] = document_view(child)
            child_payloads.append(item)
            offset += child.page_count
        meta = task_summary(root, children)
        payload.update({
            "status": meta["status"], "result": {},
            "verified_by": meta["verified_by"], "locked_by": None,
            "children": child_payloads, "child_count": meta["child_count"],
            "pending_children": meta["pending_children"],
            "status_counts": meta["status_counts"],
            "active_file_id": next((c.id for c in children
                                    if c.status == "pending_verification"), children[0].id),
        })
        return payload


class AssignBody(BaseModel):
    assignee: str


@router.post("/{file_id}/assign", dependencies=[Depends(require_role("admin"))])
async def assign(file_id: str, body: AssignBody,
                 x_user: str = Header(default="anonymous")):
    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id, for_review=True)
        f.assignee = body.assignee
        s.add(AuditLog(tenant_id=f.tenant_id, actor=_identity(x_user), action="review.assign",
                       detail={"file_id": file_id, "assignee": body.assignee}))
        await s.commit()
    return {"file_id": file_id, "assignee": body.assignee}


@router.post("/{file_id}/lock", dependencies=[Depends(require_role("operator"))])
async def lock(file_id: str, x_user: str = Header(default="anonymous")):
    """Acquire the review lock. 409 if actively held by someone else;
    expired locks are silently reclaimed (reviewer went offline, §2.6)."""
    user = _identity(x_user)
    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id, for_review=True)
        if f.status != "pending_verification":
            raise HTTPException(400, f"file not reviewable, status={f.status}")
        if f.locked_by and f.locked_by != user and not _lock_expired(f):
            raise HTTPException(409, f"locked by {f.locked_by}")
        f.locked_by = user
        f.locked_at = _now()
        await s.commit()
    return {"file_id": file_id, "locked_by": user, "ttl_minutes": 15}


@router.post("/{file_id}/unlock", dependencies=[Depends(require_role("operator"))])
async def unlock(file_id: str, x_user: str = Header(default="anonymous")):
    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id, for_review=True)
        if f.locked_by == _identity(x_user):
            f.locked_by = None
            f.locked_at = None
            await s.commit()
    return {"file_id": file_id, "locked_by": None}


class FieldEdit(BaseModel):
    record: int | None = Field(default=None, strict=True)
    field: str
    value: str = ""
    # reviewer re-drew the anchor box (M2 box-select): page-pixel space bbox
    bbox: list[float] | None = None
    page: int | None = None
    # table-field edit (M2 UX debt: 明细表可编辑): full replacement rows
    rows: list[dict] | None = None


class FieldsPatch(BaseModel):
    edits: list[FieldEdit]
    expected_revision: int | None = Field(default=None, ge=0, strict=True)


@router.patch("/{file_id}/fields", dependencies=[Depends(require_role("operator"))])
async def patch_fields(file_id: str, body: FieldsPatch,
                       x_user: str = Header(default="anonymous")):
    """Apply human corrections. Business rules: edit requires holding the lock;
    each change writes a Correction row (old->new, reviewer, skill version);
    a human-set value becomes confidence 3 and is marked $corrected."""
    user = _identity(x_user)
    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id, for_review=True)
        if f.locked_by != user or _lock_expired(f):
            raise HTTPException(423, "acquire the lock before editing")
        txn = await s.get(Transaction, f.transaction_id)
        result = deepcopy(f.result or {})
        shape = output_shape_of(f, (txn.execution_snapshot or {}).get("package") if txn else None)
        if shape == "list":
            _check_list_revision(f, body.expected_revision)
        applied = []
        for e in body.edits:
            target = result
            field_path = e.field
            if shape == "list":
                if e.field.startswith("$"):
                    raise HTTPException(400, "record metadata is not editable")
                if e.record is None:
                    raise HTTPException(400, "record index is required for List edits")
                target = _record_at(result, e.record)
                field_path = f"records[{e.record}].{e.field}"
            elif e.record is not None:
                raise HTTPException(400, "record index is only valid for List results")
            cell = target.get(e.field)
            # table fields arrive as row arrays; a rows payload replaces them
            if isinstance(cell, list) and e.rows is not None:
                import json as _json
                old_rows = _json.dumps(cell, ensure_ascii=False)
                new_rows = _json.dumps(e.rows, ensure_ascii=False)
                if old_rows == new_rows:
                    continue
                s.add(Correction(
                    tenant_id=f.tenant_id, file_id=f.id,
                    skill_code=txn.skill_code if txn else "",
                    skill_version=txn.skill_version if txn else 0,
                    field=field_path, old_value=old_rows[:4000], new_value=new_rows[:4000],
                    reviewer=user))
                target[e.field] = e.rows
                applied.append(field_path)
                continue
            if not isinstance(cell, dict):
                raise HTTPException(400, f"unknown or non-editable field: {e.field}")
            old = str(cell.get("$value") or "")
            bbox_changed = e.bbox is not None and e.bbox != cell.get("$bbox")
            if old == e.value and not bbox_changed:
                continue
            # bbox-only fixes count as corrections too (old==new marks them)
            s.add(Correction(
                tenant_id=f.tenant_id, file_id=f.id,
                skill_code=txn.skill_code if txn else "",
                skill_version=txn.skill_version if txn else 0,
                field=field_path, old_value=old, new_value=e.value, reviewer=user))
            cell = dict(cell)
            cell["$value"] = e.value
            cell["$confidence"] = 3          # human truth
            cell["$corrected"] = True
            if e.bbox is not None:
                cell["$bbox"] = e.bbox
                if e.page is not None:
                    cell["$pages"] = e.page
            target[e.field] = cell
            applied.append(field_path)
        if shape != "list":
            f.result = result
        if applied:
            # 9.15 WP6 provenance revision — must be written INSIDE this session
            # (the earlier out-of-block version silently never persisted, so
            # artifacts never regenerated after a correction; found by the
            # FX1 end-to-end test for #4)
            if shape == "list":
                # A7: a manually changed record no longer has a reliable derived outline.
                for edit in body.edits:
                    _record_at(result, edit.record).pop("$record_bbox", None)
                await _save_list_result(s, f, result, body.expected_revision)
            else:
                f.result_revision = (f.result_revision or 0) + 1
            human_event(s, "review.fields_patched", {"file_id": file_id, "fields": applied})
        await s.commit()
    return {"file_id": file_id, "corrected_fields": applied}


def _check_list_revision(file: FileRecord, expected: int | None) -> None:
    """A7: positional record edits need a revision to prevent shifted-index writes."""
    if expected is None:
        raise HTTPException(400, "expected_revision is required for List changes")
    if expected != (file.result_revision or 0):
        raise HTTPException(409, "result changed; reload before editing")


def _record_at(result: dict, index: int) -> dict:
    records = result.get("records")
    if not isinstance(records, list) or index < 0 or index >= len(records):
        raise HTTPException(400, "record index out of range")
    if not isinstance(records[index], dict):
        raise HTTPException(400, "record is not editable")
    return records[index]


async def _save_list_result(session, file: FileRecord, result: dict, expected: int, meta=None) -> None:
    # A7: compare-and-swap also protects two same-user sessions on SQLite.
    values = {"result": result, "result_revision": expected + 1}
    if meta is not None:
        values["document_meta"] = meta
    changed = await session.execute(update(FileRecord).where(
        FileRecord.id == file.id, FileRecord.tenant_id == current_tenant(),
        func.coalesce(FileRecord.result_revision, 0) == expected,
    ).values(**values).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise HTTPException(409, "result changed; reload before editing")


class RecordChange(BaseModel):
    expected_revision: int = Field(ge=0, strict=True)


async def _record_edit_context(session, file_id: str, user: str, expected: int):
    file = await require_file(session, file_id, for_review=True)
    if file.locked_by != user or _lock_expired(file):
        raise HTTPException(423, "acquire the lock before editing")
    txn = await session.get(Transaction, file.transaction_id)
    schema = (file.document_meta or {}).get("effective_schema") or (
        (txn.execution_snapshot or {}).get("package") if txn else None) or {}
    if output_shape_of(file, schema) != "list":
        raise HTTPException(400, "records are only available for List results")
    _check_list_revision(file, expected)
    return file, txn, schema


@router.delete("/{file_id}/records/{record}", dependencies=[Depends(require_role("operator"))])
async def delete_record(file_id: str, record: int, expected_revision: int | None = None,
                        x_user: str = Header(default="anonymous")):
    """A7: deleting an over-extracted record is audited and revision guarded."""
    user = _identity(x_user)
    async with session_factory()() as s:
        file, txn, _ = await _record_edit_context(s, file_id, user, expected_revision)
        result = deepcopy(file.result or {})
        removed = _record_at(result, record)
        result["records"].pop(record)
        meta = deepcopy(file.document_meta or {})
        if meta.get("table_headers"):
            shifted = {}
            for path, header in meta["table_headers"].items():
                match = re.match(r"records\[(\d+)\](.*)", path)
                if match and int(match[1]) == record:
                    continue
                if match and int(match[1]) > record:
                    path = f"records[{int(match[1]) - 1}]{match[2]}"
                shifted[path] = header
            meta["table_headers"] = shifted
        await _save_list_result(s, file, result, expected_revision, meta)
        s.add(Correction(tenant_id=file.tenant_id, file_id=file.id,
                         skill_code=txn.skill_code, skill_version=txn.skill_version,
                         field=f"records[{record}]", old_value=json.dumps(removed, ensure_ascii=False)[:4000],
                         new_value="", reviewer=user))
        human_event(s, "review.fields_patched", {"file_id": file_id, "fields": [f"records[{record}]"]})
        await s.commit()
    return {"file_id": file_id, "deleted_record": record, "record_count": len(result["records"]),
            "result_revision": expected_revision + 1}


@router.post("/{file_id}/records", dependencies=[Depends(require_role("operator"))])
async def add_record(file_id: str, body: RecordChange,
                     x_user: str = Header(default="anonymous")):
    """A7 user extension: create a blank record from the frozen execution schema."""
    user = _identity(x_user)
    async with session_factory()() as s:
        file, txn, schema = await _record_edit_context(s, file_id, user, body.expected_revision)
        result = deepcopy(file.result or {})
        records = result.get("records")
        if not isinstance(records, list):
            raise HTTPException(400, "invalid List result")
        if not schema.get("fields"):
            raise HTTPException(400, "record schema is unavailable")
        record = {}
        for field in schema["fields"]:
            record[field["name"]] = [] if field.get("type") == "table" else {
                "$value": "", "$confidence": 0, "$bbox": [], "$pages": ""}
        index = len(records)
        records.append(record)
        await _save_list_result(s, file, result, body.expected_revision)
        s.add(Correction(tenant_id=file.tenant_id, file_id=file.id,
                         skill_code=txn.skill_code, skill_version=txn.skill_version,
                         field=f"records[{index}]", old_value="",
                         new_value=json.dumps(record, ensure_ascii=False)[:4000], reviewer=user))
        human_event(s, "review.fields_patched", {"file_id": file_id, "fields": [f"records[{index}]"]})
        await s.commit()
    return {"file_id": file_id, "record_index": index, "record_count": len(records),
            "result_revision": body.expected_revision + 1}


class DecisionBody(BaseModel):
    comment: str = ""


@router.post("/{file_id}/confirm", dependencies=[Depends(require_role("operator"))])
async def confirm(file_id: str, body: DecisionBody | None = None,
                  x_user: str = Header(default="anonymous")):
    return await _decide(file_id, "passed", _identity(x_user), body.comment if body else "")


@router.post("/{file_id}/reject", dependencies=[Depends(require_role("operator"))])
async def reject(file_id: str, body: DecisionBody | None = None,
                 x_user: str = Header(default="anonymous")):
    return await _decide(file_id, "rejected", _identity(x_user), body.comment if body else "")


async def _decide(file_id: str, new_status: str, actor: str, comment: str) -> dict:
    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id, for_review=True)
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
    if new_status == "passed":        # 9.15 WP6: artifacts (re)generate on pass
        from app.tasks.output_stage import output_stage
        await output_stage(file_id)
    return {"file_id": file_id, "status": new_status, "verified_by": actor}
