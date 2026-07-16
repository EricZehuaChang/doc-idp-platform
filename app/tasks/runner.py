"""Task runner — M1 in-process jobs (KBase pattern); M2 swaps Celery+Redis with
resource-pooled queues (feasibility v2.0 §2.3). HA discipline baked in now:
the DB row is the single source of truth; steps are idempotent per
(file_id, stage); a failed file never poisons its siblings (§4.3 failure
semantics); shadow billing meters every completed file (§12.6).
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
from app.parsers.router import parse_document
from app.skillengine.schema import SkillPackage

log = logging.getLogger("idp.runner")


async def process_transaction(transaction_id: str) -> None:
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        if txn is None:
            return
        txn.status = "processing"
        await s.commit()
        files = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id))).scalars().all()
        pkg = await _load_package(s, txn)

    # files fan out in parallel (bounded), each isolated — one bad file never
    # poisons its siblings (design §4.3); M2 Celery pools replace this later
    sem = asyncio.Semaphore(4)

    async def _one(file_id: str) -> bool:
        async with sem:
            try:
                await _process_file(file_id, pkg)
                return False
            except Exception as e:
                log.exception("file %s failed", file_id)
                await _mark_error(file_id, str(e)[:500])
                return True

    results = await asyncio.gather(*(_one(f.id) for f in files))
    any_error = any(results)

    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        rows = (await s.execute(
            select(FileRecord.status).where(FileRecord.transaction_id == transaction_id))).all()
        statuses = {r[0] for r in rows}
        if statuses <= {"completed", "passed"}:
            txn.status = "completed"
        elif "pending_verification" in statuses:
            txn.status = "pending_verification"
        elif any_error and statuses <= {"error"}:
            txn.status = "error"
        else:
            txn.status = "completed"
        await s.commit()


async def _load_package(s, txn: Transaction) -> SkillPackage:
    row = (await s.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_code == txn.skill_code,
               SkillVersion.version == txn.skill_version))).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"skill version not found: {txn.skill_code} v{txn.skill_version}")
    return SkillPackage(**row.package)


async def _process_file(file_id: str, pkg: SkillPackage) -> None:
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.status = "processing"
        await s.commit()
        path, tenant = f.storage_path, f.tenant_id

    # blocking parse+extract run in a worker thread (async app stays responsive)
    udr = await asyncio.to_thread(parse_document, path, pkg.parser)
    result, usage, needs_review = await asyncio.to_thread(extract, udr, pkg)

    udr_path = Path(get_settings().data_dir) / "udr" / f"{file_id}.json"
    udr_path.parent.mkdir(parents=True, exist_ok=True)
    udr_path.write_text(udr.model_dump_json(), encoding="utf-8")

    new_status = "pending_verification" if needs_review else "completed"
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.result = json.loads(json.dumps(result, ensure_ascii=False))
        f.page_count = len(udr.pages)
        f.udr_path = str(udr_path)
        f.input_tokens = int(usage.get("prompt_tokens") or 0)
        f.output_tokens = int(usage.get("completion_tokens") or 0)
        f.status = new_status
        await s.commit()
    await shadow_meter(tenant_id=tenant, file_id=file_id,
                       pages=len(udr.pages), usage=usage)
    await webhooks.fire(tenant, f"file.{new_status}",
                        {"file_id": file_id, "status": new_status,
                         "pages": len(udr.pages)})


async def _mark_error(file_id: str, message: str) -> None:
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
    """Fire-and-forget in the running event loop. M2: replace with Celery."""
    asyncio.get_running_loop().create_task(process_transaction(transaction_id))
