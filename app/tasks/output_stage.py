"""9.15 WP6 (R13): output stage — generated download artifacts per document.

Trigger matrix (§WP6): completed -> generate; passed (review) -> generate at
the new result_revision; pending_verification -> wait; rejected/error -> no.
Failure isolation: an artifact error lives on the artifact row only — the
recognition status of the file never changes. No extra billing (B5).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import session_factory
from app.extraction.naming import (DEFAULT_RENAME, DEFAULT_SPLIT,
                                   ensure_extension, render, validate_pattern)
from app.models import FileArtifact, FileRecord, SkillVersion, Transaction
from app.parsers.base import UDR
from app.skillengine.schema import SkillPackageLoose
from app.storage import get_storage

log = logging.getLogger("idp.output")

_GENERATE_STATUSES = {"completed", "passed"}


def _config_hash(cfg) -> str:
    payload = json.dumps({"enabled": cfg.enabled, "action": cfg.action,
                          "naming_rule": cfg.naming_rule,
                          "searchable_pdf": cfg.searchable_pdf},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_pkg(snapshot: dict | None, fallback_pkg: dict | None
              ) -> SkillPackageLoose | None:
    raw = (snapshot or {}).get("package") or fallback_pkg
    if not raw:
        return None
    try:
        return SkillPackageLoose(**raw)
    except Exception:
        return None


def _load_udr(st, f: FileRecord) -> UDR | None:
    if not f.udr_path:
        return None
    try:
        return UDR.model_validate_json(st.read_bytes(f.udr_path))
    except Exception:
        return None


async def _office_to_pdf(src: str) -> bytes | None:
    """Office -> PDF via the existing LibreOffice path; None when unavailable."""
    from app.api.routes.process import PreviewConvertError, _convert_office_to_pdf, \
        _find_soffice
    soffice = _find_soffice()
    if not soffice:
        return None
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="idp-out-"))
    try:
        out = await _convert_office_to_pdf(soffice, src, work)
        return out.read_bytes()
    except (PreviewConvertError, OSError):
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# One planner per root file: a child completing and the post-fan-out root
# trigger both call in, and the rename row cannot be deduped by the unique
# index (SQLite treats NULL doc_index values as distinct).
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock


async def output_stage(file_id: str) -> None:
    """Generate/refresh download artifacts for one file. Never raises into
    the caller — every per-artifact failure lands on its own row."""
    try:
        root_id = await _root_id_of(file_id)
        if root_id is None:
            return
        async with _lock_for(root_id):
            await _output_stage_inner(file_id)
    except Exception:
        log.exception("output stage failed for %s", file_id)


async def _root_id_of(file_id: str) -> str | None:
    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None:
            return None
        return f.parent_file_id or f.id


async def _output_stage_inner(file_id: str) -> None:
    """Plan + generate artifacts for one file.

    #4/#8 fix (走查 P0): the trigger is now *document*-scoped, not entry-file
    scoped. Callers pass whichever file just finished (usually a CHILD in
    advanced mode), so the planner resolves the root first:
    - split: 每份子文档各 1 个产出，按**该文档自己的状态**决定（completed /
      passed 生成，待复核/驳回/出错不生成）；根文件被调用时遍历全部子文档。
    - rename: 整个原件 1 个产出，挂在**根文件**上，只在其全部文档都已完成或
      通过时生成；{doc_type}/{data.*} 取第 1 份文档的值（图19）。"""
    st = get_storage()
    sf = session_factory()
    async with sf() as s:
        entry = await s.get(FileRecord, file_id)
        if entry is None:
            return
        root = entry if entry.parent_file_id is None else \
            await s.get(FileRecord, entry.parent_file_id)
        if root is None:
            return
        tenant = root.tenant_id
        txn = await s.get(Transaction, root.transaction_id)
        fallback_pkg = None
        if txn is not None:
            # NOTE: `(await s.execute(...)).scalar_one_or_none()` looks right
            # but CPython parses the attribute call OUTSIDE the await — call
            # the result in a separate statement (empirically verified).
            _res = await s.execute(
                select(SkillVersion.package)
                .where(SkillVersion.skill_code == txn.skill_code,
                       SkillVersion.version == txn.skill_version))
            fallback_pkg = _res.scalar_one_or_none()
        pkg = _load_pkg((txn.execution_snapshot if txn else None), fallback_pkg)
        if pkg is None or not pkg.output.enabled or pkg.output.action == "off":
            return
        cfg = pkg.output
        cfg_hash = _config_hash(cfg)
        pattern = ensure_extension(cfg.naming_rule or
                                   (DEFAULT_RENAME if cfg.action == "rename"
                                    else DEFAULT_SPLIT))[0]
        if validate_pattern(pattern):
            return                      # bad rule: never invent names silently

        children = (await s.execute(
            select(FileRecord)
            .where(FileRecord.parent_file_id == root.id)
            .order_by(FileRecord.created_at, FileRecord.id))).scalars().all()

        # —— which documents produce an artifact, and at which revision ——
        # rename on a split file folds every child revision into the root row's
        # source_revision, so any later review/correction regenerates the name.
        targets: list[tuple[FileRecord, FileRecord, int]] = []
        if cfg.action == "split":
            docs = children if entry is root else [entry]
            targets = [(d, d, d.result_revision or 0) for d in docs
                       if d.status in _GENERATE_STATUSES]
        elif children:
            if any(c.status not in _GENERATE_STATUSES for c in children):
                return                  # 还有文档没完成/待复核：暂不生成
            agg = (root.result_revision or 0) + sum(
                c.result_revision or 0 for c in children)
            targets = [(root, children[0], agg)]
        elif entry.status in _GENERATE_STATUSES:
            targets = [(root, root, root.result_revision or 0)]
        if not targets:
            return

        original_path = st.local_path(root.storage_path)
        original_ext = Path(root.file_name).suffix
        src_udr = _load_udr(st, root)
        completed_at = root.processed_at or datetime.now(timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)

        plan_rows: list[tuple[FileArtifact, FileRecord, dict, dict, bool]] = []
        used: set[str] = set()
        for doc_file, doc, revision in targets:
            meta = (doc.document_meta or {}) if doc is not root else {}
            data = (doc.result or {}) if doc is not root else (root.result or {})
            clean: dict = {}
            for k, v in (data or {}).items():
                if isinstance(v, dict) and "$value" in v:
                    clean[k] = v.get("$value")
            doc_type = meta.get("doc_type")
            doc_index = meta.get("doc_index") if cfg.action == "split" else None
            output_is_pdf = cfg.searchable_pdf or (
                cfg.action == "split" and original_ext.lower() == ".pdf")
            name, errs = render(pattern, original_name=root.file_name,
                                original_ext=original_ext,
                                output_is_pdf=output_is_pdf,
                                doc_type=doc_type, doc_index=doc_index,
                                data=clean, completed_at=completed_at,
                                output_tz=get_settings().output_tz)
            if errs:
                continue
            # same-task dedup: " (2)", " (3)" …
            stem, dot, ext = name.rpartition(".")
            main, suffix = (stem, dot + ext) if dot else (name, "")
            candidate, n = name, 1
            while candidate.lower() in used:
                n += 1
                candidate = f"{main} ({n}){suffix}"
            used.add(candidate.lower())

            # idempotency is per (artifact file, revision, config, doc)
            existing = (await s.execute(
                select(FileArtifact).where(
                    FileArtifact.file_id == doc_file.id,
                    FileArtifact.source_revision == revision,
                    FileArtifact.config_hash == cfg_hash,
                    FileArtifact.doc_index.is_(doc_index) if doc_index is None
                    else FileArtifact.doc_index == doc_index))).scalars().all()
            # a ready row means done; a pending row means another planner got
            # here first (same revision) — only an all-error set is retried
            if existing and not all(a.status == "error" for a in existing):
                continue

            row = FileArtifact(
                tenant_id=tenant, file_id=doc_file.id, doc_index=doc_index,
                source_revision=revision, config_hash=cfg_hash,
                action=cfg.action, display_name=candidate, status="pending")
            s.add(row)
            plan_rows.append((row, doc, meta, clean, output_is_pdf))
        if not plan_rows:
            return
        await s.flush()
        # persist the pending rows NOW: the generation loop below runs in its
        # own sessions, so uncommitted rows would be invisible to it
        await s.commit()
        # replace older-revision rows of the same config AFTER the new ones
        # succeed: rows still pending here; old cleanup happens at the end
        stale = (await s.execute(
            select(FileArtifact).where(
                FileArtifact.file_id.in_([t[0].id for t in targets]),
                FileArtifact.config_hash == cfg_hash,
                FileArtifact.source_revision < max(t[2] for t in targets))
        )).scalars().all()

    # —— blocking generation outside the session ——
    for row, doc, meta, clean, output_is_pdf in plan_rows:
        try:
            await _generate_one(row, doc, meta, clean, root, cfg,
                                src_udr, original_path, original_ext,
                                output_is_pdf, st)
        except Exception as e:                  # failure isolation (§WP6)
            log.warning("artifact %s failed: %s", row.id, e)
            async with session_factory()() as s2:
                r2 = await s2.get(FileArtifact, row.id)
                if r2 is not None:
                    r2.status = "error"
                    r2.error = str(e)[:500]
                    await s2.commit()

    async with sf() as s:
        rows_now = (await s.execute(
            select(FileArtifact).where(
                FileArtifact.file_id.in_([t[0].id for t in targets]),
                FileArtifact.config_hash == cfg_hash,
                FileArtifact.source_revision == max(t[2] for t in targets))
        )).scalars().all()
        if rows_now and all(a.status == "ready" for a in rows_now) and stale:
            for a in stale:                  # newest revision wins (§WP6)
                if a.storage_key:
                    try:
                        st.remove(a.storage_key)
                    except Exception:
                        pass
                await s.delete(a)
            await s.commit()


async def _generate_one(row: FileArtifact, doc, meta, clean, root, cfg,
                        src_udr, original_path: str, original_ext: str,
                        output_is_pdf: bool, st) -> None:
    """Build one artifact blob. Semantics (§WP6):
    - rename: the original itself; searchable -> electronic PDF passthrough,
      image/scan -> ReportLab overlay (render mode 3), Office -> converted PDF
      (no coordinates => searchable=false note), OFD -> unsupported.
    - split: PDF page slice / image itself / Office->PDF; searchable layer
      uses the CHILD's sliced UDR so text lands on the right pages."""
    from app.extraction.splitter import split_pdf
    from app.files.searchable import build_searchable_pdf
    from app.parsers import raster

    searchable = False
    # `doc` is the document that names the artifact; for rename it is the first
    # document (or the root itself in standard mode), and the CONTENT always
    # comes from the root original passed in as `root`.
    is_child = doc.parent_file_id is not None
    child_udr = _load_udr(st, doc) if is_child else src_udr

    def _read(path: str) -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    if cfg.action == "rename":
        ext = original_ext.lower()
        if original_ext.lower() == ".ofd":
            raise RuntimeError("unsupported_source_format")
        if cfg.searchable_pdf:
            if ext == ".pdf":
                if await asyncio.to_thread(raster.has_text_layer, original_path):
                    out_bytes, ext, searchable = _read(original_path), ".pdf", True
                elif src_udr is not None:
                    pdf, ok = await asyncio.to_thread(
                        build_searchable_pdf, original_path, src_udr, ".pdf")
                    if pdf is None:
                        raise RuntimeError("searchable_pdf_unavailable")
                    out_bytes, ext, searchable = pdf, ".pdf", ok
                else:
                    raise RuntimeError("searchable_pdf_unavailable")
            elif ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"):
                pdf_bytes = await _office_to_pdf(original_path)
                if pdf_bytes is None:
                    raise RuntimeError("office_conversion_unavailable")
                # markitdown parses Office without coordinates: honest note
                out_bytes, ext, searchable = pdf_bytes, ".pdf", False
            else:
                if src_udr is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                pdf, ok = await asyncio.to_thread(
                    build_searchable_pdf, original_path, src_udr, ext)
                if pdf is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                out_bytes, ext, searchable = pdf, ".pdf", ok
        else:
            out_bytes = _read(original_path)
    else:
        # —— split: one artifact per child document ——
        doc_path = st.local_path(doc.storage_path) if is_child else original_path
        pages = meta.get("source_pages") or []
        if original_ext.lower() == ".ofd":
            raise RuntimeError("unsupported_source_format")
        if original_ext.lower() == ".pdf":
            if is_child and doc.storage_path.startswith("split/") \
                    and st.exists(doc.storage_path):
                out_bytes, ext = _read(doc_path), ".pdf"
            elif pages:
                tmp = Path(f"{uuid.uuid4().hex}.pdf")
                ok = await asyncio.to_thread(split_pdf, original_path, pages,
                                             str(tmp))
                out_bytes = tmp.read_bytes() if ok else b""
                tmp.unlink(missing_ok=True)
                if not out_bytes:
                    raise RuntimeError("pdf_slice_failed")
                ext = ".pdf"
            else:
                out_bytes, ext = _read(doc_path), ".pdf"
            if cfg.searchable_pdf and not await asyncio.to_thread(
                    raster.has_text_layer, original_path):
                # scanned original: overlay the child's sliced UDR
                if child_udr is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                pdf, ok = await asyncio.to_thread(
                    build_searchable_pdf, doc_path if ext == ".pdf" else
                    original_path, child_udr, ".pdf")
                if pdf is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                out_bytes, searchable = pdf, ok
        elif original_ext.lower() in (".doc", ".docx", ".xls", ".xlsx",
                                      ".ppt", ".pptx"):
            pdf_bytes = await _office_to_pdf(original_path)
            if pdf_bytes is None:
                raise RuntimeError("office_conversion_unavailable")
            out_bytes, ext = pdf_bytes, ".pdf"
            if cfg.searchable_pdf and child_udr is not None:
                pdf, ok = await asyncio.to_thread(
                    build_searchable_pdf, original_path, child_udr,
                    original_ext)
                if pdf is not None:
                    out_bytes, searchable = pdf, ok
        else:
            # image: the picture itself; searchable turns it into a PDF
            if cfg.searchable_pdf:
                if child_udr is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                pdf, ok = await asyncio.to_thread(
                    build_searchable_pdf, original_path, child_udr,
                    original_ext)
                if pdf is None:
                    raise RuntimeError("searchable_pdf_unavailable")
                out_bytes, ext, searchable = pdf, ".pdf", ok
            else:
                out_bytes, ext = _read(original_path), original_ext.lower()

    key = f"artifacts/{row.tenant_id}/{row.file_id}/{row.id}{ext}"
    st.put_bytes(key, out_bytes)
    async with session_factory()() as s:
        r2 = await s.get(FileArtifact, row.id)
        if r2 is None:
            return
        r2.status = "ready"
        r2.storage_key = key
        r2.size = len(out_bytes)
        r2.sha256 = hashlib.sha256(out_bytes).hexdigest()
        r2.searchable = searchable
        if not searchable and cfg.searchable_pdf:
            r2.error = "UDR 无坐标信息，产出未叠加文字层（searchable=false）"
        await s.commit()

