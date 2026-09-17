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
from app.extraction.classifier import ClassificationError, plan_documents
from app.extraction.pipeline import extract
from app.integrations import webhooks
from app.models import FileRecord, SkillVersion, Transaction
from app.parsers.base import UDR
from app.parsers.router import escalate_if_tables_missing, parse_document
from app.skillengine.effective import category_subpackage
from app.skillengine.schema import SkillPackage, SkillPackageLoose
from app.storage import get_storage


def skill_expects_tables(pkg: SkillPackage, deps: dict | None = None) -> bool:
    """A skill whose EFFECTIVE fields include a table needs row/column
    structure from the parser; drives the table-escalation rule in parse_stage.
    Advanced mode: the union over every category's fields — inline fields plus
    referenced packages' fields (§WP4 step 2). v1/standard verdict unchanged
    (protected asset 3). Entity-list tables (PII sweeps) hold document-wide
    hits, not layout tables — they must not push an electronic document onto
    the paid OCR tier."""
    from app.skillengine.schema import SkillPackageLoose

    def _has_table(fields) -> bool:
        return any(f.type == "table" and not f.entity_list for f in fields)

    if _has_table(pkg.fields):
        return True
    for cat in getattr(pkg, "categories", None) or []:
        if cat.handler == "existing_skill":
            ref_code = cat.skill_ref.skill_code if cat.skill_ref else ""
            dep = (deps or {}).get(ref_code or "")
            if dep:
                dpkg = SkillPackageLoose(**dep["package"])
                if _has_table(dpkg.fields):
                    return True
        elif _has_table(cat.fields):
            return True
    return False

log = logging.getLogger("idp.runner")


async def process_transaction(transaction_id: str) -> None:
    """In-process backend: fan out files (bounded) and finalize."""
    plan = await plan_transaction(transaction_id)
    if plan is None:
        return
    pkg, deps, file_ids = plan
    sem = asyncio.Semaphore(4)

    async def _one(file_id: str) -> None:
        async with sem:
            try:
                await parse_stage(file_id, pkg.parser, skill_expects_tables(pkg, deps))
                await extract_stage(file_id, pkg, deps)
            except Exception as e:
                log.exception("file %s failed", file_id)
                await mark_error(file_id, str(e)[:500])

    await asyncio.gather(*(_one(fid) for fid in file_ids))
    await finalize_transaction(transaction_id)


async def plan_transaction(transaction_id: str) -> tuple[SkillPackage, dict, list[str]] | None:
    """Mark the transaction processing and return (package, deps, file_ids)."""
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        if txn is None:
            return None
        txn.status = "processing"
        await s.commit()
        files = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id))).scalars().all()
        pkg, deps = await _load_package(s, txn)
    return pkg, deps, [f.id for f in files]


async def _load_package(s, txn: Transaction) -> tuple[SkillPackage, dict]:
    """Execution config: prefer the immutable submit-time snapshot (WP4 — a
    referenced skill publishing a new version later must not change this
    task); legacy tasks without a snapshot keep the version-row read."""
    snap = txn.execution_snapshot or {}
    if snap.get("package"):
        # loose read: stored rows must load even with unknown keys (§3.3)
        return SkillPackageLoose(**snap["package"]), dict(snap.get("dependencies") or {})
    row = (await s.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_code == txn.skill_code,
               SkillVersion.version == txn.skill_version))).scalar_one_or_none()
    if row is None:
        raise RuntimeError(f"skill version not found: {txn.skill_code} v{txn.skill_version}")
    return SkillPackageLoose(**row.package), {}


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


async def extract_stage(file_id: str, pkg: SkillPackage,
                        deps: dict | None = None) -> None:
    """Stage 2: UDR (from disk) -> extraction result + billing + webhook.
    Skips quietly when parse never landed (chain redelivery after a parse
    failure) — the error state is already on the row.

    Multi-doc split (M2 item 7) happens here, before extraction:
    - advanced mode (9.15 WP4): the category classifier produces a DocumentPlan
      and EVERY plan entry becomes a child file (even a single document, so the
      data shape is uniform); children run concurrently with failure isolation.
    - v1/standard: the legacy page classifier may fan a bundle out; unchanged.
    The parent ends in status "split" in both paths."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.status == "error" or not f.udr_path:
            return
        tenant, udr_path = f.tenant_id, f.udr_path
        is_child = f.parent_file_id is not None
        already_split = f.status == "split"
        # BYOK + custom-channel cache warm (§11.10): resolution inside the
        # worker thread is sync and cannot reach the DB
        from app.extraction import byok, custom_providers
        await byok.warm(s, tenant)
        await custom_providers.warm(s, tenant)

    udr = UDR.model_validate_json(get_storage().read_bytes(udr_path))

    advanced = getattr(pkg, "skill_mode", "standard") == "advanced"
    if not is_child and not already_split and advanced:
        # 重投幂等: a split parent keeps its children — never classify again
        cats = [c.model_dump() if hasattr(c, "model_dump") else dict(c)
                for c in (pkg.categories or [])]
        try:
            plan, class_usage = await asyncio.to_thread(
                plan_documents, udr, cats,
                pkg.document_layout or "single",
                pkg.classification_rules or "",
                (pkg.model_binding.extractor or None))
        except ClassificationError as e:
            # 分类失败: the whole file fails, no silent Other fallback;
            # error rows bill nothing (settle counts result-bearing rows only)
            await mark_error(file_id, f"classification_failed: {e}")
            return
        meta = await _fan_out_children(file_id, udr, plan, pkg, deps or {})
        await shadow_meter(tenant_id=tenant, file_id=file_id,
                           pages=0, usage=class_usage)   # classification tokens
        await webhooks.fire(tenant, "file.split",
                            {"file_id": file_id, "children": meta["child_ids"],
                             "documents": len(meta["child_ids"]),
                             "doc_types": meta["doc_types"]})
        # children run concurrently (IDP_DOC_CONCURRENCY, default 4); one child
        # failing marks ONLY that child — the siblings and the split parent are
        # untouched (D3 semantics applied to the new path from day one)
        sem = asyncio.Semaphore(get_settings().doc_concurrency)

        async def _child(cid: str, subpkg: SkillPackage | None,
                         classify_only: bool = False) -> None:
            async with sem:
                try:
                    if classify_only:
                        await _complete_classify_only(cid)
                    else:
                        await extract_stage(cid, subpkg, deps)
                except Exception as e:
                    log.exception("child %s failed", cid)
                    await mark_error(cid, str(e)[:500])

        await asyncio.gather(*(_child(c["child_id"], c["subpkg"],
                                      c.get("classify_only", False))
                               for c in meta["children"]))
        return

    # children never re-split (bounded recursion); "off" kills the feature
    if (not is_child and not already_split and not advanced
            and get_settings().multi_doc_split == "auto" and len(udr.pages) >= 2):
        from app.extraction.splitter import classify_pages
        groups, split_usage = await asyncio.to_thread(classify_pages, udr)
        if len(groups) > 1:
            meta = await _fan_out_children(file_id, udr, [
                {"pages": g, "category_id": None} for g in groups], pkg, deps or {})
            await shadow_meter(tenant_id=tenant, file_id=file_id,
                               pages=0, usage=split_usage)   # classification tokens
            await webhooks.fire(tenant, "file.split",
                                {"file_id": file_id, "children": meta["child_ids"],
                                 "documents": len(meta["child_ids"]),
                                 "doc_types": meta["doc_types"]})
            # D3 fix: a child exception must not bubble into the parent's error
            # state nor strand its siblings in "processing"
            sem = asyncio.Semaphore(get_settings().doc_concurrency)

            async def _child(cid: str, subpkg: SkillPackage | None) -> None:
                async with sem:
                    try:
                        await extract_stage(cid, subpkg, deps)
                    except Exception as e:
                        log.exception("child %s failed", cid)
                        await mark_error(cid, str(e)[:500])

            await asyncio.gather(*(_child(c["child_id"], c["subpkg"])
                                   for c in meta["children"]))
            return
    await _extract_one(file_id, udr, pkg, deps or {})


async def _complete_classify_only(file_id: str) -> None:
    """classify_only document: no extraction requested (B4 default: pages still
    bill) — result stays {}, status completed, webhook still fires."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        tenant = f.tenant_id
        f.result = {}
        f.status = "completed"
        f.processed_at = datetime.now(timezone.utc)
        meta = dict(f.document_meta or {})
        meta["extraction_status"] = "not_requested"
        f.document_meta = meta
        await s.commit()
        pages = f.page_count
    await shadow_meter(tenant_id=tenant, file_id=file_id, pages=pages, usage={})
    await webhooks.fire(tenant, "file.completed",
                        {"file_id": file_id, "status": "completed", "pages": pages})


async def _extract_one(file_id: str, udr: UDR, pkg: SkillPackage,
                       deps: dict) -> None:
    """Single-document extraction + persistence + billing + webhook."""
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        tenant = f.tenant_id
    result, usage, needs_review = await asyncio.to_thread(extract, udr, pkg)

    new_status = "pending_verification" if needs_review else "completed"
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        f.result = json.loads(json.dumps(result, ensure_ascii=False))
        f.input_tokens = int(usage.get("prompt_tokens") or 0)
        f.output_tokens = int(usage.get("completion_tokens") or 0)
        f.status = new_status
        f.processed_at = datetime.now(timezone.utc)   # per-doc speed figure (§task ledger)
        meta = dict(f.document_meta or {})
        meta["extraction_status"] = "completed"
        f.document_meta = meta
        await s.commit()
        pages = f.page_count
    await shadow_meter(tenant_id=tenant, file_id=file_id, pages=pages, usage=usage)
    await webhooks.fire(tenant, f"file.{new_status}",
                        {"file_id": file_id, "status": new_status, "pages": pages})


async def _fan_out_children(file_id: str, udr: UDR,
                            plan: list[dict], pkg: SkillPackage,
                            deps: dict) -> dict:
    """Create one child FileRecord per document in the plan: sliced UDR on
    disk, physical PDF slice when possible (split_pdf copies page ranges, so
    non-consecutive plans work), parent marked split.

    Advanced mode also stamps `document_meta` (source_pages/doc_index/doc_type/
    category_id/handler/effective_schema) and derives each child's subpackage
    (inline fields / referenced package / classify-only). Legacy splitting keeps
    its old shape: no document_meta, the parent package for every child.
    Returns {"child_ids", "doc_types", "children": [{"child_id", "subpkg"}]}."""
    from app.extraction.splitter import slice_udr, split_pdf

    st = get_storage()
    sf = session_factory()
    advanced = getattr(pkg, "skill_mode", "standard") == "advanced"
    cat_by_id = {c["id"]: c for c in (
        [c.model_dump() if hasattr(c, "model_dump") else dict(c)
         for c in (pkg.categories or [])] if advanced else [])}
    # snapshot deps arrive as {code: {"version", "package": dict}} — the
    # subpackage derivation wants package objects
    pinned = {code: SkillPackageLoose(**v["package"])
              for code, v in (deps or {}).items()}
    child_ids: list[str] = []
    children: list[dict] = []
    doc_types: list[str | None] = []
    async with sf() as s:
        parent = await s.get(FileRecord, file_id)
        stem, suffix = Path(parent.file_name).stem, Path(parent.file_name).suffix
        for i, entry in enumerate(plan, start=1):
            pages, cat_id = entry["pages"], entry.get("category_id")
            cat = cat_by_id.get(cat_id) or {}
            handler = cat.get("handler") if advanced else None
            doc_type = cat.get("doc_type") if advanced else None
            subpkg: SkillPackage | None = pkg
            meta: dict = {}
            if advanced:
                subpkg = category_subpackage(pkg, cat_id, pinned_packages=pinned)
                meta = {"doc_index": i, "doc_type": doc_type,
                        "category_id": cat_id, "handler": handler,
                        "source_pages": list(pages),
                        "effective_schema": json.loads(subpkg.model_dump_json())}
            child = FileRecord(
                tenant_id=parent.tenant_id, transaction_id=parent.transaction_id,
                parent_file_id=parent.id, file_name=f"{stem}#doc{i}{suffix}",
                storage_path=parent.storage_path, status="processing",
                page_count=len(pages), document_meta=meta or None)
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
            children.append({"child_id": child.id, "subpkg": subpkg,
                             "classify_only": advanced and handler == "classify_only"})
            doc_types.append(doc_type)
        parent.status = "split"
        await s.commit()
    return {"child_ids": child_ids, "doc_types": doc_types, "children": children}


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
