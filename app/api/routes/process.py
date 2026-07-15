"""Processing API (design v0.2 §7, aligned with Insavlo public API shape):
POST /api/v1/process, GET /api/v1/status/{transaction_id}.
"""
import hashlib
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.db import session_factory
from app.models import FileRecord, Skill, SkillVersion, Transaction
from app.tasks import runner
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1", tags=["process"])

_MAX_FILES = 10                    # limits aligned with Insavlo v1.2.6
_MAX_SIZE = 50 * 1024 * 1024


class SubmitResponse(BaseModel):
    success: bool = True
    transaction_id: str
    files: list[dict]


@router.post("/process", response_model=SubmitResponse, status_code=202)
async def submit(files: list[UploadFile] = File(...), skill_code: str = Form(...)):
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"max {_MAX_FILES} files per request")
    tenant = current_tenant()
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

        out = []
        store_dir = Path(get_settings().data_dir) / "files" / tenant / txn.id
        store_dir.mkdir(parents=True, exist_ok=True)
        for up in files:
            blob = await up.read()
            if len(blob) > _MAX_SIZE:
                raise HTTPException(413, f"file too large: {up.filename}")
            # content-hash name: dedupe-friendly, no path injection from filename
            digest = hashlib.sha256(blob).hexdigest()[:16]
            suffix = Path(up.filename or "file").suffix.lower()
            path = store_dir / f"{digest}{suffix}"
            path.write_bytes(blob)
            rec = FileRecord(tenant_id=tenant, transaction_id=txn.id,
                             file_name=up.filename or path.name, storage_path=str(path))
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
        return FileResponse(f.storage_path, filename=f.file_name)


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
        files = []
        for f in rows:
            result = f.result
            if result is not None and not include_confidence_flag:
                result = {k: (v.get("$value") if isinstance(v, dict) else v)
                          for k, v in result.items()}
            files.append({
                "file_id": f.id, "file_name": f.file_name, "status": f.status,
                "page_count": f.page_count, "msg": f.error or "",
                "input_tokens": f.input_tokens, "output_tokens": f.output_tokens,
                "result": result,
            })
        return {"success": True, "transaction_id": txn.id, "status": txn.status,
                "skill_code": txn.skill_code, "skill_version": txn.skill_version,
                "files": files}
