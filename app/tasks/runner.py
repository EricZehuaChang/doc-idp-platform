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
from pathlib import Path

from sqlalchemy import select

from app.billing.ledger import shadow_meter
from app.config import get_settings
from app.db import session_factory
from app.extraction.pipeline import extract
from app.integrations import webhooks
from app.models import FileRecord, SkillVersion, Transaction
from app.parsers.base import UDR
from app.parsers.router import parse_document
from app.skillengine.schema import SkillPackage

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
                await parse_stage(file_id, pkg.parser)
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


async def parse_stage(file_id: str, parser_pin: str | None) -> None:
    """Stage 1: document -> UDR persisted on disk + page_count in DB."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.status = "processing"
        await s.commit()
        path = f.storage_path

    # blocking parse runs in a worker thread (async app stays responsive)
    udr = await asyncio.to_thread(parse_document, path, parser_pin)

    udr_path = Path(get_settings().data_dir) / "udr" / f"{file_id}.json"
    udr_path.parent.mkdir(parents=True, exist_ok=True)
    udr_path.write_text(udr.model_dump_json(), encoding="utf-8")

    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.page_count = len(udr.pages)
        f.udr_path = str(udr_path)
        await s.commit()


async def extract_stage(file_id: str, pkg: SkillPackage) -> None:
    """Stage 2: UDR (from disk) -> extraction result + billing + webhook.
    Skips quietly when parse never landed (chain redelivery after a parse
    failure) — the error state is already on the row."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.status == "error" or not f.udr_path:
            return
        tenant, udr_path = f.tenant_id, f.udr_path

    udr = UDR.model_validate_json(Path(udr_path).read_text(encoding="utf-8"))
    result, usage, needs_review = await asyncio.to_thread(extract, udr, pkg)

    new_status = "pending_verification" if needs_review else "completed"
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.result = json.loads(json.dumps(result, ensure_ascii=False))
        f.input_tokens = int(usage.get("prompt_tokens") or 0)
        f.output_tokens = int(usage.get("completion_tokens") or 0)
        f.status = new_status
        await s.commit()
        pages = f.page_count
    await shadow_meter(tenant_id=tenant, file_id=file_id, pages=pages, usage=usage)
    await webhooks.fire(tenant, f"file.{new_status}",
                        {"file_id": file_id, "status": new_status, "pages": pages})


async def finalize_transaction(transaction_id: str) -> None:
    """Roll file states up into the transaction row (state machine truth)."""
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        rows = (await s.execute(
            select(FileRecord.status).where(FileRecord.transaction_id == transaction_id))).all()
        statuses = {r[0] for r in rows}
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
