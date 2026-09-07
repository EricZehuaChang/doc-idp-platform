"""Task pipeline stages + in-process runner (lite tier). M2: the same stages
are wrapped as Celery tasks in resource-pooled queues (design v0.2 §9 table:
parse-gpu/parse-cpu/extract; feasibility v2.0 §2.3) — IDP_QUEUE_BACKEND picks.

HA discipline: the DB row is the single source of truth; stages are idempotent
per (file_id, stage) — parse persists the UDR to disk, extract re-reads it, so
either stage can be redelivered safely; a failed file never poisons its
siblings (§4.3); shadow billing meters every completed file (§12.6).
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.billing.ledger import shadow_meter
from app.config import get_settings
from app.db import session_factory
from app.extraction.pipeline import extract
from app.integrations import webhooks
from app.models import FileRecord, SkillVersion, Transaction
from app.parsers.base import UDR
from app.parsers.router import escalate_if_tables_missing, parse_document
from app.skillengine.schema import SkillPackage
from app.storage import get_storage


def skill_expects_tables(pkg: SkillPackage) -> bool:
    """A skill with table-typed fields needs row/column structure from the
    parser; drives the table-escalation rule in parse_stage. Entity-list
    tables (PII sweeps) hold document-wide hits, not layout tables — they
    must not push an electronic document onto the paid OCR tier."""
    return any(f.type == "table" and not f.entity_list for f in pkg.fields)

log = logging.getLogger("idp.runner")


async def process_transaction(transaction_id: str) -> None:
    """In-process backend: fan out files (bounded) and finalize."""
    plan = await plan_transaction(transaction_id)
    if plan is None:
        return
    pkg, file_ids = plan
    sem = asyncio.Semaphore(4)

    async def _one(file_id: str) -> None:
        async with sem:
            try:
                await parse_stage(file_id, pkg.parser, skill_expects_tables(pkg))
                await extract_stage(file_id, pkg)
            except Exception as e:
                log.exception("file %s failed", file_id)
                await mark_error(file_id, str(e)[:500])

    await asyncio.gather(*(_one(fid) for fid in file_ids))
    await finalize_transaction(transaction_id)


async def plan_transaction(transaction_id: str) -> tuple[SkillPackage, list[str]] | None:
    """Mark the transaction processing and return (package, file_ids)."""
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        if txn is None:
            return None
        txn.status = "processing"
        await s.commit()
        files = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id))).scalars().all()
        pkg = await _load_package(s, txn)
    return pkg, [f.id for f in files]


async def _load_package(s, txn: Transaction) -> SkillPackage:
    row = (await s.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_code == txn.skill_code,
               SkillVersion.version == txn.skill_version))).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"skill version not found: {txn.skill_code} v{txn.skill_version}")
    return SkillPackage(**row.package)


async def parse_stage(file_id: str, parser_pin: str | None,
                      expects_tables: bool = False) -> None:
    """Stage 1: document -> UDR persisted on disk + page_count in DB.

    expects_tables (default False keeps old queued Celery messages valid):
    when the skill declares table fields and the free structured parse found
    no tables, re-parse via the scan-tier engine (router escalation rule).
    A pinned parser is an explicit operator choice and is never overridden."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.status = "processing"
        await s.commit()
        path = get_storage().local_path(f.storage_path)

    # blocking parse runs in a worker thread (async app stays responsive)
    udr = await asyncio.to_thread(parse_document, path, parser_pin)
    if expects_tables and not parser_pin:
        udr = await asyncio.to_thread(escalate_if_tables_missing, path, udr)

    udr_key = get_storage().put_bytes(
        f"udr/{file_id}.json", udr.model_dump_json().encode("utf-8"))

    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.page_count = len(udr.pages)
        f.udr_path = udr_key
        await s.commit()


async def extract_stage(file_id: str, pkg: SkillPackage) -> None:
    """Stage 2: UDR (from disk) -> extraction result + billing + webhook.
    Skips quietly when parse never landed (chain redelivery after a parse
    failure) — the error state is already on the row.

    Multi-doc split (M2 item 7) happens here, before extraction: an LLM page
    classifier may fan the bundle out into child files, each extracted on its
    own; the parent ends in status "split"."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.status == "error" or not f.udr_path:
            return
        tenant, udr_path = f.tenant_id, f.udr_path
        is_child = f.parent_file_id is not None
        # BYOK + custom-channel cache warm (§11.10): resolution inside the
        # worker thread is sync and cannot reach the DB
        from app.extraction import byok, custom_providers
        await byok.warm(s, tenant)
        await custom_providers.warm(s, tenant)

    udr = UDR.model_validate_json(get_storage().read_bytes(udr_path))

    # children never re-split (bounded recursion); "off" kills the feature
    if not is_child and get_settings().multi_doc_split == "auto" and len(udr.pages) >= 2:
        from app.extraction.splitter import classify_pages
        groups, split_usage = await asyncio.to_thread(classify_pages, udr)
        if len(groups) > 1:
            child_ids = await _fan_out_children(file_id, udr, groups)
            await shadow_meter(tenant_id=tenant, file_id=file_id,
                               pages=0, usage=split_usage)   # classification tokens
            await webhooks.fire(tenant, "file.split",
                                {"file_id": file_id, "children": child_ids,
                                 "documents": len(child_ids)})
            for cid in child_ids:
                await extract_stage(cid, pkg)
            return
    result, usage, needs_review = await asyncio.to_thread(extract, udr, pkg)

    new_status = "pending_verification" if needs_review else "completed"
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.result = json.loads(json.dumps(result, ensure_ascii=False))
        f.input_tokens = int(usage.get("prompt_tokens") or 0)
        f.output_tokens = int(usage.get("completion_tokens") or 0)
        f.status = new_status
        f.processed_at = datetime.now(timezone.utc)   # per-doc speed figure (§task ledger)
        await s.commit()
        pages = f.page_count
    await shadow_meter(tenant_id=tenant, file_id=file_id, pages=pages, usage=usage)
    await webhooks.fire(tenant, f"file.{new_status}",
                        {"file_id": file_id, "status": new_status, "pages": pages})


async def _fan_out_children(file_id: str, udr: UDR, groups: list[list[int]]) -> list[str]:
    """Create one child FileRecord per detected document: sliced UDR on disk,
    physical PDF slice when possible, parent marked split."""
    from app.extraction.splitter import slice_udr, split_pdf

    st = get_storage()
    sf = session_factory()
    child_ids: list[str] = []
    async with sf() as s:
        parent = await s.get(FileRecord, file_id)
        stem, suffix = Path(parent.file_name).stem, Path(parent.file_name).suffix
        for i, pages in enumerate(groups, start=1):
            child = FileRecord(
                tenant_id=parent.tenant_id, transaction_id=parent.transaction_id,
                parent_file_id=parent.id, file_name=f"{stem}#doc{i}{suffix}",
                storage_path=parent.storage_path, status="processing",
                page_count=len(pages))
            s.add(child)
            await s.flush()
            c_udr = slice_udr(udr, pages)
            child.udr_path = st.put_bytes(
                f"udr/{child.id}.json", c_udr.model_dump_json().encode("utf-8"))
            split_key = f"split/{child.id}.pdf"
            if await asyncio.to_thread(split_pdf, st.local_path(parent.storage_path),
                                       pages, st.local_path(split_key)):
                child.storage_path = split_key
            child_ids.append(child.id)
        parent.status = "split"
        await s.commit()
    return child_ids


async def finalize_transaction(transaction_id: str) -> None:
    """Roll file states up into the transaction row (state machine truth).
    "split" parents count as settled — their children carry the work.

    Money settles BEFORE the rollup commit (§12.2 lifecycle alignment): a
    terminal transaction status must imply the freeze is already released —
    status pollers act on it. Settle reads per-file statuses, which the stages
    committed already. A settle failure must not block the status rollup —
    log and leave the freeze for manual reconciliation (full ledger trail)."""
    try:
        from app.billing.engine import settle
        await settle(transaction_id)
    except Exception:
        log.exception("billing settle failed txn=%s", transaction_id)
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        rows = (await s.execute(
            select(FileRecord.status).where(FileRecord.transaction_id == transaction_id))).all()
        statuses = {r[0] for r in rows}
        statuses.discard("split")
        if statuses <= {"completed", "passed"}:
            txn.status = "completed"
        elif "pending_verification" in statuses:
            txn.status = "pending_verification"
        elif statuses and statuses <= {"error"}:
            txn.status = "error"
        else:
            txn.status = "completed"
        await s.commit()


async def mark_error(file_id: str, message: str) -> None:
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.status = "error"
        f.error = message
        f.processed_at = datetime.now(timezone.utc)   # failed run still has a duration
        tenant = f.tenant_id
        await s.commit()
    await webhooks.fire(tenant, "file.error",
                        {"file_id": file_id, "error": message[:200]})


def submit(transaction_id: str) -> None:
    """Dispatch seam. celery backend ships the transaction to the pooled
    queues; inprocess (lite default) keeps the M1 fire-and-forget task."""
    if get_settings().queue_backend == "celery":
        from app.tasks.celery_tasks import run_transaction
        from app.tenancy import current_tenant
        run_transaction.delay(transaction_id, current_tenant())
    else:
        asyncio.get_running_loop().create_task(process_transaction(transaction_id))
