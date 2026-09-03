"""Processing API (design v0.2 §7, aligned with Insavlo public API shape):
POST /api/v1/process, GET /api/v1/status/{transaction_id}.
"""
import asyncio
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.api.task_groups import summary as task_summary
from app.billing import engine as billing
from app.config import get_settings
from app.db import session_factory
from app.models import FileRecord, Skill, SkillVersion, Transaction
from app.tasks import runner
from app.tenancy import current_actor, current_tenant

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


@router.post("/process", response_model=SubmitResponse, status_code=202)
async def submit(files: list[UploadFile] = File(...), skill_code: str = Form(...)):
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
    sf = session_factory()
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

        txn = Transaction(tenant_id=tenant, skill_code=skill_code, skill_version=ver.version)
        s.add(txn)
        await s.flush()

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
        store_dir = Path(get_settings().data_dir) / "files" / tenant / txn.id
        store_dir.mkdir(parents=True, exist_ok=True)
        for name, blob in blobs:
            # content-hash name: dedupe-friendly, no path injection from filename
            digest = hashlib.sha256(blob).hexdigest()[:16]
            suffix = Path(name).suffix.lower()
            path = store_dir / f"{digest}{suffix}"
            path.write_bytes(blob)
            rec = FileRecord(tenant_id=tenant, transaction_id=txn.id,
                             file_name=name or path.name, storage_path=str(path))
            s.add(rec)
            await s.flush()
            out.append({"file_id": rec.id, "original_filename": rec.file_name})
        await s.commit()
        txn_id = txn.id

    runner.submit(txn_id)          # atomicity: rows committed before enqueue (§2.6 HA)
    return SubmitResponse(transaction_id=txn_id, files=out)


@router.get("/files/{file_id}/download")
async def download(file_id: str):
    """Original file stream — the left pane of the dual-screen review UI."""
    from fastapi.responses import FileResponse

    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.tenant_id != current_tenant():
            raise HTTPException(404, "file not found")
        # content is immutable per file_id (content-hash storage): let the
        # browser cache it — second open of the review page renders instantly
        # (frontend caching design v0.2 §9.0 layer ③)
        return FileResponse(f.storage_path, filename=f.file_name,
                            headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.get("/files/{file_id}/preview")
async def preview(file_id: str):
    """Renderable original for the review left pane (需求3: 对照原件加载不出来,
    Doc 格式不支持预览). PDFs and images stream as-is; Office files (.doc/.docx/
    .xls/.xlsx/.ppt/.pptx) are converted to PDF via LibreOffice and cached.
    Formats with no converter (e.g. OFD) answer 422 — the UI falls back to the
    download link instead of showing a blank pane."""
    from fastapi.responses import FileResponse

    sf = session_factory()
    async with sf() as s:
        f = await s.get(FileRecord, file_id)
        if f is None or f.tenant_id != current_tenant():
            raise HTTPException(404, "file not found")
        suffix = Path(f.file_name).suffix.lower()
        src = f.storage_path
        stem = Path(f.file_name).stem
    immutable = {"Cache-Control": "private, max-age=86400, immutable"}
    if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        return FileResponse(src, filename=f"{stem}{suffix}", headers=immutable)
    if suffix not in _OFFICE_PREVIEW:
        raise HTTPException(422, f"{suffix} 格式暂不支持原件预览，请下载原件查看")
    cache_dir = Path(get_settings().data_dir) / "preview"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{file_id}.pdf"
    if cached.exists() and cached.stat().st_size > 0:
        return FileResponse(cached, filename=f"{stem}.pdf", headers=immutable)
    soffice = _find_soffice()
    if not soffice:
        raise HTTPException(422, "该格式的原件预览需要服务器安装 LibreOffice，"
                                 "当前服务器未安装；请下载原件查看")
    async with _preview_lock:
        # re-check under the lock: a concurrent request may have just converted
        if cached.exists() and cached.stat().st_size > 0:
            return FileResponse(cached, filename=f"{stem}.pdf", headers=immutable)
        work_dir = cache_dir / f"work-{file_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            out = await _convert_office_to_pdf(soffice, src, work_dir)
            os.replace(out, cached)               # atomic cache write
        except PreviewConvertError as e:
            raise HTTPException(422, f"原件转换失败：{e}") from e
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    return FileResponse(cached, filename=f"{stem}.pdf", headers=immutable)


@router.get("/status/{transaction_id}")
async def status(transaction_id: str, include_confidence_flag: bool = True):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        txn = await s.get(Transaction, transaction_id)
        if txn is None or txn.tenant_id != tenant:   # cross-tenant probe -> 404 (§11.2 CI case)
            raise HTTPException(404, "transaction not found")
        rows = (await s.execute(
            select(FileRecord).where(FileRecord.transaction_id == transaction_id))).scalars().all()

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
            return {
                "file_id": f.id, "file_name": f.file_name, "status": f.status,
                "page_count": f.page_count, "msg": f.error or "",
                "input_tokens": f.input_tokens, "output_tokens": f.output_tokens,
                "result": result, "parent_file_id": f.parent_file_id,
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
