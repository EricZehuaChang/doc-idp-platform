"""Processing API (design v0.2 §7, aligned with Insavlo public API shape):
POST /api/v1/process, GET /api/v1/status/{transaction_id}.
"""
import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import human_event
from app.api.task_groups import summary as task_summary
from app.billing import engine as billing
from app import references
from app.config import get_settings
from app.db import session_factory
from app.models import FileRecord, Skill, SkillVersion, Transaction
from app.skillengine.schema import SkillPackageLoose
from app.storage import get_storage
from app.tasks import runner
from app.tenancy import current_actor, current_tenant, require_role

from app.visibility import require_file, require_txn, visible_file_cond

router = APIRouter(prefix="/api/v1", tags=["process"])

_MAX_FILES = 10                    # limits aligned with Insavlo v1.2.6
_MAX_SIZE = 50 * 1024 * 1024
# Per-request body budget for the browser upload page. The production entry
# (nginx :5004) caps bodies at 100m, so 10 x 50MB would be rejected by the proxy
# with an HTML error the SPA cannot parse. The UI batches its selection against
# this number instead; the API itself is unchanged for existing integrations.
_MAX_BATCH = 80 * 1024 * 1024

# —— original-document preview (需求3): Office formats convert to PDF on the
# server so the review left pane can render them; the conversion result is
# cached per file_id (storage is content-hash immutable). ——
_OFFICE_PREVIEW = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
_preview_lock = asyncio.Lock()


class PreviewConvertError(RuntimeError):
    """LibreOffice ran but produced nothing usable."""


# —— WP1: a DB row whose blob is gone from this host (e.g. rows written on the
# old Windows box before the storage-key migration) must answer a
# distinguishable 404 instead of an unhandled 500 from FileResponse. The
# message stays action-safe: no disk paths, nothing implying retry can help. ——
_ORIGINAL_MISSING = {
    "code": "original_missing",
    "message": "原件暂不可用，请联系管理员恢复或重新上传",
}


def _original_readable(st, key: str) -> bool:
    """True when the row's blob can actually be served. Read-only on purpose:
    unlike `local_path()` this must never create directories."""
    try:
        return st.exists(key) and st.size(key) > 0
    except OSError:
        return False


def _find_soffice() -> str | None:
    """Locate a LibreOffice binary: explicit env override, then PATH, then the
    known install locations (dev WIN / production Linux / dev Mac)."""
    env = os.environ.get("IDP_SOFFICE")
    candidates = [env] if env else []
    candidates += [shutil.which("soffice"), shutil.which("soffice.exe"),
                   "/usr/bin/soffice", "/usr/bin/libreoffice",
                   r"D:\Program Files\LibreOffice\program\soffice.exe",
                   r"C:\Program Files\LibreOffice\program\soffice.exe",
                   "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    for cand in candidates:
        if cand and Path(cand).exists():
            return str(cand)
    return None


async def _convert_office_to_pdf(soffice: str, src: str, work_dir: Path) -> Path:
    """Run LibreOffice headless in a worker thread. stdout/stderr go to a file
    (never a pipe), one fresh UserInstallation profile per run so parallel
    conversions cannot fight over the LO profile lock."""
    def _run() -> Path:
        profile = f"file:///{(work_dir / f'lo-{uuid.uuid4().hex[:8]}').as_posix()}"
        log_path = work_dir / "soffice.log"
        cmd = [soffice, "--headless", "--norestore", "--convert-to", "pdf",
               "--outdir", str(work_dir), f"-env:UserInstallation={profile}", src]
        try:
            with open(log_path, "w", encoding="utf-8") as logf:
                subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT,
                               timeout=180, check=False)
        except subprocess.TimeoutExpired as e:
            raise PreviewConvertError("转换超时（180 秒）") from e
        out = work_dir / f"{Path(src).stem}.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise PreviewConvertError("转换未产出 PDF")
        return out
    return await asyncio.to_thread(_run)


class SubmitResponse(BaseModel):
    success: bool = True
    transaction_id: str
    files: list[dict]


class FormatsResponse(BaseModel):
    """Upload capability contract (browser upload page reads this instead of
    hardcoding a list — UI and parser support cannot drift apart)."""
    extensions: list[str]
    max_files: int
    max_size_mb: int
    max_batch_mb: int


@router.get("/formats", response_model=FormatsResponse)
async def formats():
    from app.parsers.router import UPLOAD_SUFFIXES

    return FormatsResponse(
        extensions=list(UPLOAD_SUFFIXES), max_files=_MAX_FILES,
        max_size_mb=_MAX_SIZE // (1024 * 1024),
        max_batch_mb=_MAX_BATCH // (1024 * 1024))


@router.post("/process", response_model=SubmitResponse, status_code=202,
             dependencies=[Depends(require_role("operator"))])
async def submit(files: list[UploadFile] = File(...), skill_code: str = Form(...),
                 idem_header: str | None = Header(default=None, alias="Idempotency-Key")):
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"max {_MAX_FILES} files per request")
    tenant = current_tenant()
    # blobs first: the billing gate needs a page estimate before any row lands
    blobs: list[tuple[str, bytes]] = []
    for up in files:
        blob = await up.read()
        if len(blob) > _MAX_SIZE:
            raise HTTPException(413, f"file too large: {up.filename}")
        blobs.append((up.filename or "", blob))

    # —— 9.15 R21 initiator snapshot: fixed at submit time, never re-derived ——
    actor = current_actor()
    if actor.get("user_id"):
        initiator = {"type": "user", "id": actor["name"], "label": actor["name"],
                     "user_id": actor["user_id"], "api_key_id": None}
    elif actor.get("api_key_id"):
        key_name = actor["name"].split(":", 1)[-1]
        if actor.get("key_type") == "agent":
            # personal agent key: "owner email (key name)"; accountless: key name
            label = (f"{actor['owner_email']}（{key_name}）"
                     if actor.get("owner_email") else key_name)
        else:
            label = f"应用：{key_name}"
        initiator = {"type": "api_key", "id": actor["name"], "label": label,
                     "user_id": None, "api_key_id": actor["api_key_id"]}
    else:
        initiator = {"type": "anonymous", "id": "anonymous", "label": "anonymous",
                     "user_id": None, "api_key_id": None}

    # —— 9.15 §3.7: agent keys may only submit within their skill scope; the
    # owner's role gates the act of submitting (viewer-owned key = read-only) ——
    if actor.get("key_type") == "agent":
        from app.tenancy import _ROLE_RANK
        if _ROLE_RANK.get(actor.get("role", ""), -1) < _ROLE_RANK["operator"]:
            raise HTTPException(403, detail={
                "code": "role_required",
                "message": "该 Key 的所有者为只读角色（viewer），不能提交任务"})
        allowed = actor.get("allowed_skill_codes")
        if allowed is not None and skill_code not in allowed:
            raise HTTPException(403, detail={
                "code": "skill_not_allowed_for_key",
                "message": f"技能 {skill_code} 不在该 Key 的可用技能范围内"})

    # —— 9.15 WP2 submit idempotency: same key+payload replay returns the
    # original transaction; different payload under the same key is a 409 ——
    idem_key = (idem_header or "").strip() or None
    if idem_key and len(idem_key) > 128:
        raise HTTPException(400, "Idempotency-Key must be ≤128 characters")
    principal = (f"key:{initiator['api_key_id']}" if initiator["api_key_id"]
                 else f"user:{initiator['user_id']}" if initiator["user_id"]
                 else "anonymous")
    fingerprint = None
    if idem_key:
        digest = hashlib.sha256()
        digest.update(skill_code.encode())
        for _, blob in sorted(blobs, key=lambda x: hashlib.sha256(x[1]).hexdigest()):
            digest.update(hashlib.sha256(blob).digest())
        fingerprint = digest.hexdigest()

    sf = session_factory()

    async def _replay(sess, prior) -> SubmitResponse:
        """Hand back the original submission: same transaction, no re-freeze."""
        rows = (await sess.execute(
            select(FileRecord).where(
                FileRecord.transaction_id == prior.id,
                FileRecord.parent_file_id.is_(None)))).scalars().all()
        return SubmitResponse(
            transaction_id=prior.id,
            files=[{"file_id": f.id, "original_filename": f.file_name} for f in rows])

    async with sf() as s:
        skill = await s.get(Skill, skill_code)
        if skill is None or skill.tenant_id != tenant or skill.state != "active":
            raise HTTPException(404, f"skill not found: {skill_code}")
        ver = (await s.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_code == skill_code,
                   SkillVersion.status == "published")
            .order_by(SkillVersion.version.desc()))).scalars().first()
        if ver is None:
            raise HTTPException(400, f"skill has no published version: {skill_code}")

        if idem_key:
            from datetime import datetime, timedelta, timezone as _tz
            prior = (await s.execute(
                select(Transaction)
                .where(Transaction.tenant_id == tenant,
                       Transaction.idem_principal == principal,
                       Transaction.idempotency_key == idem_key)
                .order_by(Transaction.created_at.desc()))).scalars().first()
            if prior is not None:
                age = (datetime.now(_tz.utc)
                       - (prior.created_at if prior.created_at.tzinfo
                          else prior.created_at.replace(tzinfo=_tz.utc)))
                if age <= timedelta(hours=24):
                    if prior.request_fingerprint == fingerprint:
                        return await _replay(s, prior)
                    raise HTTPException(409, detail={
                        "code": "idempotency_conflict",
                        "message": "同一 Idempotency-Key 已用于不同内容的请求，"
                                   "请更换 Key 或去掉该请求头"})
                # expired: release the slot for the new submission
                prior.idempotency_key = None
                prior.idem_principal = None

        txn = Transaction(
            tenant_id=tenant, skill_code=skill_code, skill_version=ver.version,
            initiator_type=initiator["type"], initiator_id=initiator["id"],
            initiator_label=initiator["label"], initiator_user_id=initiator["user_id"],
            api_key_id=initiator["api_key_id"],
            idempotency_key=idem_key, idem_principal=principal if idem_key else None,
            request_fingerprint=fingerprint)
        s.add(txn)
        try:
            await s.flush()
        # 9.15 WP4 (§3.4): freeze the execution config now — package + resolved
        # dependency packages (R10). Stored on the txn row in the same DB
        # transaction; legacy rows keep snapshot=None.
            txn.execution_snapshot = await references.build_execution_snapshot(
                s, tenant, skill_code, ver.version)
        except references.ReferenceUnavailable as e:
            raise HTTPException(409, detail={
                "code": "reference_unavailable",
                "message": f"技能引用的依赖不存在：{e.skill_code}"
                           + (f" v{e.version}" if e.version else "")})
        except IntegrityError:
            # racing duplicate lost the unique-index race: roll back and hand
            # back the winner's transaction (or 409 if payloads differ) —
            # never a 500 and never a double billing freeze (§ WP2 idempotency)
            await s.rollback()
            async with sf() as s2:
                prior = (await s2.execute(
                    select(Transaction)
                    .where(Transaction.tenant_id == tenant,
                           Transaction.idem_principal == principal,
                           Transaction.idempotency_key == idem_key)
                    .order_by(Transaction.created_at.desc()))).scalars().first()
            if prior is None:
                raise
            if prior.request_fingerprint == fingerprint:
                return await _replay(s2, prior)
            raise HTTPException(409, detail={
                "code": "idempotency_conflict",
                "message": "同一 Idempotency-Key 已用于不同内容的请求，"
                           "请更换 Key 或去掉该请求头"})

        # 9.15 WP5: fast-mode page cap at submit (§WP5) — estimate via the
        # existing billing heuristic; the parse stage re-checks with real pages
        pkg_probe = SkillPackageLoose(**(ver.package or {}))
        if getattr(pkg_probe, "processing_mode", "balanced") == "fast":
            cap = get_settings().fast_max_pages
            for name, blob in blobs:
                if billing.estimate_pages(blob, Path(name).suffix) > cap:
                    raise HTTPException(422, detail={
                        "code": "fast_mode_page_limit",
                        "message": f"文件 {name} 预计超过极速模式 {cap} 页上限，"
                                   "请改用均衡模式"})
        # billing gate (§12.2): live mode freezes estimated pages x rate inside
        # this same DB transaction — a 402 rolls everything back. Owner Root
        # (unlimited) skips the money gate but stays metered and audited (§12.7).
        cfg = await billing.load_config(s)
        actor = current_actor()
        if cfg["mode"] == "live" and not actor.get("unlimited"):
            rate = billing.rate_for(cfg, skill.kind,
                                    await billing.is_byok_tenant(s, tenant))
            pages_est = sum(billing.estimate_pages(b, Path(n).suffix) for n, b in blobs)
            # allocated API keys pay from their own carved-out budget (§12.7)
            key_id = (actor.get("api_key_id")
                      if actor.get("quota_mode") == "allocated" else None)
            try:
                await billing.freeze(s, tenant, txn.id, pages_est, rate, key_id=key_id)
            except billing.InsufficientCredit as e:
                if e.payer == "key":
                    raise HTTPException(402, "该 API Key 的独立额度不足:本次预估需 "
                                        f"{e.required:g} credit,Key 可用 {e.available:g}。"
                                        "请管理员为该 Key 划拨额度。")
                raise HTTPException(402, "余额不足:本次预估需 "
                                    f"{e.required:g} credit,当前可用 {e.available:g}。"
                                    "请充值后重试(失败页不会扣费)。")

        out = []
        st = get_storage()
        for name, blob in blobs:
            # content-hash name: dedupe-friendly, no path injection from filename
            digest = hashlib.sha256(blob).hexdigest()[:16]
            suffix = Path(name).suffix.lower()
            key = st.put_bytes(f"files/{tenant}/{txn.id}/{digest}{suffix}", blob)
            rec = FileRecord(tenant_id=tenant, transaction_id=txn.id,
                             file_name=name or Path(key).name, storage_path=key)
            s.add(rec)
            await s.flush()
            out.append({"file_id": rec.id, "original_filename": rec.file_name})
        human_event(s, "files.submitted", {"transaction_id": txn.id, "skill_code": skill_code,
                    "files": [{"file_name": name, "estimated_pages": billing.estimate_pages(blob, Path(name).suffix)} for name, blob in blobs]})
        await s.commit()
        txn_id = txn.id

    runner.submit(txn_id)          # atomicity: rows committed before enqueue (§2.6 HA)
    return SubmitResponse(transaction_id=txn_id, files=out)


_IMMUTABLE = {"Cache-Control": "private, max-age=86400, immutable",
              "Vary": "Authorization"}


@router.get("/files/{file_id}/download")
async def download(file_id: str, save: bool = False):
    """Original file stream — the left pane of the dual-screen review UI.

    R4 (2026-09-22): the viewer and hover prefetch fetch this same URL, so only
    an explicit save (``?save=1``, the 下载原件 buttons) is an audited download;
    viewing stays cacheable and out of the operation log."""
    from fastapi.responses import FileResponse

    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id)
        if f is None or f.tenant_id != current_tenant():
            raise HTTPException(404, "file not found")
        st = get_storage()
        if not _original_readable(st, f.storage_path):
            raise HTTPException(404, detail=_ORIGINAL_MISSING)
        if save:
            human_event(s, "files.downloaded", {"file_id": f.id})
            await s.commit()
            return FileResponse(st.local_path(f.storage_path), filename=f.file_name,
                                headers={"Cache-Control": "private, no-store"})
        # content is immutable per file_id (content-hash storage): let the
        # browser cache it — second open of the review page renders instantly
        # (frontend caching design v0.2 §9.0 layer ③). Vary keeps one browser's
        # cache from serving it across accounts now that visibility is per user.
        return FileResponse(st.local_path(f.storage_path),
                            filename=f.file_name, headers=_IMMUTABLE)


@router.get("/files/{file_id}/preview")
async def preview(file_id: str):
    """Renderable original for the review left pane (需求3: 对照原件加载不出来,
    Doc 格式不支持预览). PDFs and images stream as-is; Office files (.doc/.docx/
    .xls/.xlsx/.ppt/.pptx) are converted to PDF via LibreOffice and cached.
    Formats with no converter (e.g. OFD) answer 422 — the UI falls back to the
    download link instead of showing a blank pane. WP1: a row whose original is
    missing answers 404 original_missing; an Office file may still serve its
    converted cache, and LibreOffice never runs against a nonexistent source."""
    from fastapi.responses import FileResponse

    sf = session_factory()
    async with sf() as s:
        f = await require_file(s, file_id)
        if f is None or f.tenant_id != current_tenant():
            raise HTTPException(404, "file not found")
        suffix = Path(f.file_name).suffix.lower()
        stem = Path(f.file_name).stem
    st = get_storage()
    immutable = _IMMUTABLE
    cached_key = f"preview/{file_id}.pdf"
    has_cache = (suffix in _OFFICE_PREVIEW
                 and st.exists(cached_key) and st.size(cached_key) > 0)
    if not _original_readable(st, f.storage_path):
        if not has_cache:
            raise HTTPException(404, detail=_ORIGINAL_MISSING)
        return FileResponse(st.local_path(cached_key), filename=f"{stem}.pdf",
                            headers=immutable)
    if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        return FileResponse(st.local_path(f.storage_path),
                            filename=f"{stem}{suffix}", headers=immutable)
    if suffix not in _OFFICE_PREVIEW:
        raise HTTPException(422, f"{suffix} 格式暂不支持原件预览，请下载原件查看")
    if has_cache:
        return FileResponse(st.local_path(cached_key), filename=f"{stem}.pdf",
                            headers=immutable)
    soffice = _find_soffice()
    if not soffice:
        raise HTTPException(422, "该格式的原件预览需要服务器安装 LibreOffice，"
                                 "当前服务器未安装；请下载原件查看")
    src = st.local_path(f.storage_path)   # original exists: mkdir here is safe
    async with _preview_lock:
        # re-check under the lock: a concurrent request may have just converted
        if st.exists(cached_key) and st.size(cached_key) > 0:
            return FileResponse(st.local_path(cached_key), filename=f"{stem}.pdf",
                                headers=immutable)
        work_dir = Path(tempfile.mkdtemp(prefix=f"idp-preview-{file_id}-"))
        try:
            out = await _convert_office_to_pdf(soffice, src, work_dir)
            st.put_file(cached_key, str(out))     # atomic-enough cache write
        except PreviewConvertError as e:
            raise HTTPException(422, f"原件转换失败：{e}") from e
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    return FileResponse(st.local_path(cached_key), filename=f"{stem}.pdf",
                        headers=immutable)


@router.get("/transactions/{transaction_id}/documents")
async def transaction_documents(transaction_id: str):
    """9.15 WP5 (§3.5): per-root-file document view with clean data.

    - Standard mode: the root file itself is doc_index=1 (doc_type null,
      source_pages = all pages).
    - Advanced mode: one document per child (document_meta drives the shape).
    - `data` carries clean values ($value scalars, $-less table rows; List
      mode renders the records array); `review_fields` is computed HERE so the
      frontend never re-derives runner decisions.
    - Access: production -> tenant users, agent keys only their own tasks;
      test -> operator+ only, agent keys always 404 (§3.9)."""
    from app.api.routes.docmeta import document_view, page_range
    tenant = current_tenant()
    actor = current_actor()
    sf = session_factory()
    async with sf() as s:
        txn = await require_txn(s, transaction_id)
        if txn is None or txn.tenant_id != tenant:
            raise HTTPException(404, "transaction not found")
        is_agent = actor.get("key_type") == "agent"
        if txn.purpose == "test":
            if is_agent:
                raise HTTPException(404, "transaction not found")
            from app.tenancy import has_role
            if not has_role("operator"):
                raise HTTPException(403, detail={
                    "code": "role_required",
                    "message": "测试任务仅对 operator 及以上开放"})
        # R5: key ownership is enforced by require_txn (api_key_id, not name)

        files = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id, visible_file_cond())
            .order_by(FileRecord.created_at, FileRecord.id))).scalars().all()
        # 9.15 WP6: artifacts for every file of the txn (incl. failure reasons)
        from app.models import FileArtifact
        artifacts_by_file: dict[str, list[dict]] = {}
        if files:
            art_rows = (await s.execute(
                select(FileArtifact)
                .where(FileArtifact.tenant_id == tenant,
                       FileArtifact.file_id.in_([f.id for f in files]),
                       FileArtifact.status != "pending")
                .order_by(FileArtifact.created_at))).scalars().all()
            for a in art_rows:
                artifacts_by_file.setdefault(a.file_id, []).append({
                    "artifact_id": a.id, "name": a.display_name,
                    "status": a.status, "error": a.error, "size": a.size,
                    "searchable": a.searchable})
        roots = [f for f in files if f.parent_file_id is None]
        children_by_parent: dict[str, list[FileRecord]] = {}
        for f in files:
            if f.parent_file_id:
                children_by_parent.setdefault(f.parent_file_id, []).append(f)

        snapshot_pkg = None
        if txn.execution_snapshot:
            snapshot_pkg = txn.execution_snapshot.get("package") or {}
        else:
            # legacy rows (pre-WP4): fall back to the version row like the runner
            ver = (await s.execute(
                select(SkillVersion).where(
                    SkillVersion.skill_code == txn.skill_code,
                    SkillVersion.version == txn.skill_version))).scalars().first()
            snapshot_pkg = (ver.package or {}) if ver else {}
        threshold = 2
        output_shape = "object"
        if snapshot_pkg:
            output_shape = snapshot_pkg.get("output_shape") or "object"
            rp = snapshot_pkg.get("review_policy") or {}
            threshold = int(rp.get("confidence_threshold") or 2)

        def _clean(v):
            if isinstance(v, dict):
                return v.get("$value")
            if isinstance(v, list):
                return [{k: cv for k, cv in r.items() if not k.startswith("$")}
                        if isinstance(r, dict) else r for r in v]
            return v

        def _review_fields(result: dict) -> list[str]:
            names = []
            for name, cell in (result or {}).items():
                if not isinstance(cell, dict) or cell.get("$confidence") is None:
                    continue   # fast mode: unscored -> nothing is "pending"
                if (cell.get("$confidence", 3) < threshold
                        or cell.get("$rule_failures")
                        or cell.get("$format_error")):
                    names.append(name)
            return names

        def _extraction_status(f: FileRecord) -> str:
            """#7: meta first, file status as the honest fallback (D8: rejected
            already HAS its extraction — the rejection is a review state)."""
            meta = f.document_meta or {}
            if meta.get("extraction_status"):
                return str(meta["extraction_status"])
            return {"queued": "processing", "processing": "processing",
                    "completed": "completed", "passed": "completed",
                    "pending_verification": "completed", "rejected": "completed",
                    "error": "failed"}.get(f.status, "processing")

        def _documents(f: FileRecord) -> list[dict]:
            kids = children_by_parent.get(f.id)
            if kids:
                docs = []
                for k in kids:
                    view = document_view(k) or {}
                    docs.append({
                        "file_id": k.id, "doc_index": view.get("doc_index"),
                        "doc_type": view.get("doc_type"),
                        "category_id": view.get("category_id"),
                        "handler": view.get("handler"),
                        "source_pages": view.get("source_pages") or [],
                        "page_range": view.get("page_range") or page_range(
                            view.get("source_pages") or []),
                        "extraction_status": view.get("extraction_status"),
                        "error": k.error or None,
                        "data": _clean_data(k),
                        **({"field_sources": k.document_meta["field_sources"]} if (k.document_meta or {}).get("field_sources") else {}),
                        "review_fields": _review_fields(k.result or {}),
                        "metrics": (k.document_meta or {}).get("metrics"),
                        "artifacts": artifacts_by_file.get(k.id, [])})
                return docs
            pages = list(range(1, (f.page_count or 0) + 1))
            return [{
                "file_id": f.id, "doc_index": 1, "doc_type": None,
                "category_id": None, "handler": None,
                "source_pages": pages, "page_range": page_range(pages),
                # #7 (D8): the runner stamps extraction_status when it finishes;
                # falling back to the file status must not report an extracted,
                # waiting-for-review file as still processing (agents polled on)
                "extraction_status": _extraction_status(f),
                "error": f.error or None,
                "data": _clean_data(f),
                **({"field_sources": f.document_meta["field_sources"]} if (f.document_meta or {}).get("field_sources") else {}),
                "review_fields": _review_fields(f.result or {}),
                "metrics": (f.document_meta or {}).get("metrics"),
                "artifacts": artifacts_by_file.get(f.id, [])}]

        def _clean_data(f: FileRecord):
            result = f.result
            if result is None:
                return None
            if output_shape == "list":
                rows = result.get("records")
                if not isinstance(rows, list):
                    return []
                return [{k: cv for k, cv in r.items() if not k.startswith("$")}
                        if isinstance(r, dict) else r for r in rows]
            return {k: _clean(v) for k, v in result.items()}

        return {
            "transaction_id": txn.id, "purpose": txn.purpose,
            "status": txn.status, "skill_code": txn.skill_code,
            "skill_version": txn.skill_version,
            "files": [{
                "file_id": f.id, "file_name": f.file_name,
                "status": f.status, "page_count": f.page_count,
                # D3 (#8): file-level artifacts, new key only — rename produces
                # ONE file for the whole original, so it hangs off the root and
                # must be visible at file level, not only inside a document
                "artifacts": artifacts_by_file.get(f.id, []),
                "documents": _documents(f)} for f in roots]}


@router.get("/status/{transaction_id}")
async def status(transaction_id: str, include_confidence_flag: bool = True):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        txn = await require_txn(s, transaction_id)
        if txn is None or txn.tenant_id != tenant:   # cross-tenant probe -> 404 (§11.2 CI case)
            raise HTTPException(404, "transaction not found")
        actor = current_actor()
        if actor.get("key_type") == "agent" and txn.purpose == "test":
            # §3.9: Playground test runs are invisible to agent keys
            raise HTTPException(404, "transaction not found")
        rows = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id, visible_file_cond()))).scalars().all()

        def file_payload(f: FileRecord) -> dict:
            result = f.result
            if result is not None and not include_confidence_flag:
                # plain-value view: scalars collapse to $value, table rows drop
                # $-metadata ($cells) so integrators get clean row objects
                def _plain(v):
                    if isinstance(v, dict):
                        return v.get("$value")
                    if isinstance(v, list):
                        return [{ck: cv for ck, cv in r.items()
                                 if not ck.startswith("$")}
                                if isinstance(r, dict) else r for r in v]
                    return v
                result = {k: _plain(v) for k, v in result.items()}
            from app.api.routes.docmeta import document_view
            return {
                "file_id": f.id, "file_name": f.file_name, "status": f.status,
                "page_count": f.page_count, "msg": f.error or "",
                "input_tokens": f.input_tokens, "output_tokens": f.output_tokens,
                "result": result, "parent_file_id": f.parent_file_id,
                # 9.15 WP4 (§3.6): per-document view, new key only — absent for
                # legacy/standard files without classification metadata
                "document": document_view(f),
            }

        # One API row per uploaded file. Internal split jobs remain available as
        # explicit children instead of masquerading as six independent uploads.
        roots = [f for f in rows if f.parent_file_id is None]
        by_parent: dict[str, list[FileRecord]] = {}
        for f in rows:
            if f.parent_file_id:
                by_parent.setdefault(f.parent_file_id, []).append(f)
        files = []
        for root in roots:
            children = sorted(by_parent.get(root.id, []), key=lambda x: (x.created_at, x.id))
            meta = task_summary(root, children)
            item = file_payload(root)
            item.update({
                "status": meta["status"], "msg": meta["error"] or "",
                "child_count": meta["child_count"],
                "pending_children": meta["pending_children"],
                "status_counts": meta["status_counts"],
                "children": [file_payload(c) for c in children],
            })
            files.append(item)
        return {"success": True, "transaction_id": txn.id, "status": txn.status,
                "skill_code": txn.skill_code, "skill_version": txn.skill_version,
                "files": files}
