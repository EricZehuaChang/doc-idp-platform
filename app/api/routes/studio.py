"""Skill Studio endpoints (9.15 WP3, §3.6): editor sample files + combined
field generation. Samples are tenant-scoped helper uploads for the DESIGN
stage — sensitive data, operator+ only (viewer 403, §3.7), never production
documents. The generated field draft is returned, never persisted.
"""
import asyncio
import hashlib
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from app.db import session_factory
from app.models import StudioSample
from app.parsers.router import UPLOAD_SUFFIXES, parse_document
from app.storage import get_storage
from app.skillengine import studio
from app.tenancy import current_actor, current_tenant, require_role

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])

_MAX_SIZE = 50 * 1024 * 1024
_udr_lock = asyncio.Lock()      # parse once per sample (UDR cache writes)

# new-interface error shape (§3.6): require_role already answers structured
# 403s; 404s here stay existence-hiding for cross-tenant probes


async def _own_sample(s, sample_id: str) -> StudioSample:
    row = await s.get(StudioSample, sample_id)
    if row is None or row.tenant_id != current_tenant():
        raise HTTPException(404, detail={"code": "sample_not_found",
                                         "message": "样本不存在"})
    return row


@router.post("/samples", status_code=201)
async def upload_sample(file: UploadFile = File(...),
                        skill_code: str | None = Form(default=None)):
    """Upload one editor sample. Original is content-hash keyed; when a skill
    is given its pinned parser pre-parses a UDR and caches it alongside."""
    require_role("operator")()
    blob = await file.read()
    if len(blob) > _MAX_SIZE:
        raise HTTPException(413, detail={"code": "sample_too_large",
                                         "message": "样本不能超过 50 MB"})
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(400, detail={
            "code": "sample_type_rejected",
            "message": f"不支持的样本类型 {suffix or '(无后缀)'}，"
                       f"可用：{'、'.join(UPLOAD_SUFFIXES)}"})

    tenant = current_tenant()
    sample_id = uuid.uuid4().hex
    st = get_storage()
    digest = hashlib.sha256(blob).hexdigest()[:16]
    key = st.put_bytes(f"studio/{tenant}/{sample_id}/{digest}{suffix}", blob)

    pages: list[dict] = []
    parse_error = None
    if skill_code:
        try:
            pinned = None       # per-skill parser resolution happens in WP3 editor;
            udr = await asyncio.to_thread(parse_document, st.local_path(key), pinned)
            pages = [{"page_no": p.page_no, "width": p.width, "height": p.height}
                     for p in udr.pages]
            st.put_bytes(f"studio/{tenant}/{sample_id}/udr.json",
                         json.dumps({"pages": pages,
                                     "full_markdown": udr.full_markdown or ""},
                                    ensure_ascii=False).encode())
        except Exception as e:                      # pre-parse is best-effort
            parse_error = str(e)[:200]

    sf = session_factory()
    async with sf() as s:
        row = StudioSample(tenant_id=tenant, skill_code=skill_code,
                           uploader_id=current_actor().get("user_id") or "",
                           file_name=file.filename or Path(key).name,
                           storage_key=key)
        s.add(row)
        await s.commit()
        return {"id": row.id, "file_name": row.file_name, "skill_code": skill_code,
                "pages": pages, "parse_error": parse_error,
                "created_at": row.created_at.isoformat()}


@router.get("/samples")
async def list_samples(skill_code: str | None = None):
    require_role("operator")()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        q = select(StudioSample).where(StudioSample.tenant_id == tenant)
        if skill_code:
            q = q.where(StudioSample.skill_code == skill_code)
        rows = (await s.execute(q.order_by(StudioSample.created_at.desc())
                                )).scalars().all()
        return {"samples": [{"id": r.id, "file_name": r.file_name,
                             "skill_code": r.skill_code, "created_at": r.created_at.isoformat()}
                            for r in rows]}


@router.delete("/samples/{sample_id}", status_code=204)
async def delete_sample(sample_id: str):
    require_role("operator")()
    sf = session_factory()
    async with sf() as s:
        row = await _own_sample(s, sample_id)
        await s.delete(row)
        await s.commit()
    st = get_storage()
    for key in (row.storage_key,
                f"studio/{current_tenant()}/{sample_id}/udr.json"):
        try:
            st.delete(key)
        except OSError:
            pass          # row is gone; orphaned blobs are sweepable
    return None


@router.get("/samples/{sample_id}/file")
async def sample_file(sample_id: str):
    """Original stream for the editor sample panel (DocStage)."""
    require_role("operator")()
    sf = session_factory()
    async with sf() as s:
        row = await _own_sample(s, sample_id)
    from fastapi.responses import FileResponse
    st = get_storage()
    if not st.exists(row.storage_key):
        raise HTTPException(404, detail={"code": "sample_not_found",
                                         "message": "样本文件已被清理"})
    return FileResponse(st.local_path(row.storage_key), filename=row.file_name)


class GenerateBody(BaseModel):
    sample_id: str | None = None
    description: str | None = None
    provider: str | None = None


@router.post("/generate-fields")
async def generate_fields(body: GenerateBody):
    """样本预标注 + 描述生成 二合一：字段范围以描述为准，名称/类型/示例值参考
    样本。只返回草稿，不落库。至少给一个来源。"""
    require_role("operator")()
    if not (body.description or "").strip() and not body.sample_id:
        raise HTTPException(400, detail={
            "code": "no_input",
            "message": "请提供样本或字段描述中的至少一项"})
    sample_text = ""
    pages: list[dict] = []
    if body.sample_id:
        sf = session_factory()
        async with sf() as s:
            row = await _own_sample(s, body.sample_id)
        st = get_storage()
        cache_key = f"studio/{current_tenant()}/{row.id}/udr.json"
        if st.exists(cache_key):
            cached = json.loads(st.read_bytes(cache_key))
            pages = cached.get("pages", [])
            sample_text = cached.get("full_markdown", "")
        else:
            try:
                udr = await asyncio.to_thread(parse_document,
                                              st.local_path(row.storage_key), None)
                sample_text = udr.full_markdown or udr.full_text()
            except Exception as e:
                raise HTTPException(422, detail={
                    "code": "sample_parse_failed",
                    "message": f"样本解析失败：{str(e)[:160]}"})
    await _warm()
    from app.extraction.provider_client import ProviderError
    try:
        out = await asyncio.to_thread(
            studio.generate_fields, sample_text, body.description or "",
            body.provider)
    except ProviderError as e:
        raise HTTPException(502, f"模型通道暂时不可用，请稍后重试：{str(e)[:200]}")
    if not out["fields"]:
        raise HTTPException(422, detail={
            "code": "no_fields_generated",
            "message": "未能生成字段草稿，请补充描述或更换样本"})
    return {"fields": [f.model_dump() for f in out["fields"]],
            "examples": out["examples"], "pages": pages,
            "provider_used": out["provider_used"]}


async def _warm() -> None:
    """Token-burning generation runs in a worker thread — warm BYOK + custom
    channels first (same discipline as skills.py._warm_byok)."""
    from app.extraction import byok, custom_providers
    sf = session_factory()
    async with sf() as s:
        await byok.warm(s, current_tenant())
        await custom_providers.warm(s, current_tenant())
