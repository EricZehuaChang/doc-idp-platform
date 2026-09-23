"""Skill Studio endpoints (9.15 WP3, §3.6): editor sample files + combined
field generation. Samples are tenant-scoped helper uploads for the DESIGN
stage — sensitive data, operator+ only (viewer 403, §3.7), never production
documents. The generated field draft is returned, never persisted.
"""
import asyncio
import hashlib
import json
import uuid
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_, select

from app.db import session_factory
from app.skillengine.schema import SkillPackageLoose
from app.models import (FileRecord, Skill, SkillVersion, StudioRun,
                        StudioSample, Transaction)
from app.parsers.router import UPLOAD_SUFFIXES, parse_document
from app.storage import get_storage
from app.tasks import runner
from app.skillengine import studio
from app.tenancy import current_actor, current_tenant, require_role

from app.visibility import require_file, visible_file_cond

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


@router.patch("/samples/{sample_id}")
async def adopt_sample(sample_id: str, payload: dict):
    """D6 (#20): assign an existing sample to a skill code. Used right after a
    new skill is created, so samples uploaded on the 新建 page stop being
    orphaned (they were uploaded with skill_code empty on purpose)."""
    require_role("operator")()
    tenant = current_tenant()
    code = str(payload.get("skill_code") or "").strip()
    if not code:
        raise HTTPException(422, detail={"code": "skill_code_required",
                                         "message": "缺少 skill_code"})
    sf = session_factory()
    async with sf() as s:
        row = await _own_sample(s, sample_id)
        skill = await s.get(Skill, code)
        if skill is None or skill.tenant_id != tenant:
            raise HTTPException(404, "skill not found")
        row.skill_code = code
        await s.commit()
        return {"id": row.id, "skill_code": row.skill_code}


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


@router.get("/reference-skills")
async def reference_skills(exclude: str | None = None):
    """9.15 WP4 (R10): skills eligible for 「使用已有技能」 references —
    same tenant, enabled, with a published version that is standard-mode and
    not the skill being edited. Only standard-mode skills can be referenced,
    so references can never nest or cycle."""
    sf = session_factory()
    tenant = current_tenant()
    async with sf() as s:
        q = (select(Skill, SkillVersion)
             .join(SkillVersion, (SkillVersion.skill_code == Skill.code)
                   & (SkillVersion.tenant_id == Skill.tenant_id))
             .where(Skill.tenant_id == tenant, Skill.state == "active",
                    SkillVersion.status == "published")
             .order_by(Skill.code, SkillVersion.version.desc()))
        out, seen = [], set()
        for skill, ver in (await s.execute(q)).all():
            if skill.code in seen or skill.code == exclude:
                continue
            if (ver.package or {}).get("skill_mode") == "advanced":
                continue        # only standard-mode skills are referenceable
            seen.add(skill.code)
            pkg = SkillPackageLoose(**(ver.package or {}))
            out.append({"skill_code": skill.code, "name": skill.name,
                        "published_version": ver.version,
                        "field_count": len(pkg.fields),
                        # read-only field digest for the reference banner (图16)
                        "fields": [{"name": f.name, "type": f.type,
                                    "instruction": f.instruction}
                                   for f in pkg.fields],
                        "updated_at": skill.updated_at})
    return {"skills": out}


# —— 9.15 WP5 Playground (R14, 图21) ——


class RunRequest(BaseModel):
    skill_code: str
    version: int | None = None      # None = latest row of any status (draft ok)
    sample_ids: list[str]


# D2 (#6): the StudioRun row is written once at submit; status and duration
# are DERIVED at read time from the file records instead of adding a second
# write path. Vocabulary: queued / running / completed / needs_review / failed.
_FILE_TO_RUN_STATUS = {
    "queued": "queued", "processing": "running", "completed": "completed",
    "passed": "completed", "pending_verification": "needs_review",
    "error": "failed", "rejected": "failed",
    # a split parent's own state says nothing about progress — its children
    # carry the real per-document state, so it contributes nothing here
    "split": None,
}
_RUN_SETTLED = {"completed", "needs_review", "failed"}


def _derive_run(r: StudioRun, file_rows: list) -> dict:
    """file_rows = the run's file + (for a split parent) its children."""
    rows = [f for f in file_rows if f is not None]
    if not rows:
        status = _FILE_TO_RUN_STATUS.get(r.status, r.status)
        return {"status": status, "duration_ms": r.duration_ms,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None}
    derived = [d for d in (_FILE_TO_RUN_STATUS.get(f.status, f.status)
                           for f in rows) if d is not None]
    if not derived:
        derived = ["running"]
    if "failed" in derived:
        status = "failed"
    elif all(d == "completed" for d in derived):
        status = "completed"
    elif "needs_review" in derived and not any(
            d in ("queued", "running") for d in derived):
        status = "needs_review"
    elif all(d == "needs_review" for d in derived):
        status = "needs_review"
    elif any(d == "queued" for d in derived):
        status = "queued"
    else:
        status = "running"

    def _aware(dt):
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    # submission = the earliest row (the root is created first); completion =
    # the last DOCUMENT producing a result (a split parent has no processed_at
    # of its own, so it must not veto the duration)
    state_rows = [f for f in rows
                  if _FILE_TO_RUN_STATUS.get(f.status, f.status) is not None]         or rows
    starts = [x for x in (_aware(f.created_at) for f in rows) if x]
    ends = [x for x in (_aware(f.processed_at) for f in state_rows) if x]
    duration = None
    finished = None
    if starts and len(ends) == len(state_rows):
        finished = max(ends)
        duration = max(int((finished - min(starts)).total_seconds() * 1000), 1)
    elif ends:
        finished = max(ends)
    return {"status": status, "duration_ms": duration,
            "finished_at": finished.isoformat() if finished else None}


def _run_view(r: StudioRun, derived: dict | None = None) -> dict:
    d = derived or {"status": r.status, "duration_ms": r.duration_ms,
                    "finished_at": None}
    return {"run_id": r.id, "skill_code": r.skill_code,
            "version": r.skill_version, "sample_id": r.sample_id,
            "transaction_id": r.transaction_id, "file_id": r.file_id,
            "status": d["status"], "processing_mode": r.processing_mode,
            "duration_ms": d["duration_ms"], "created_by": r.created_by,
            "created_at": r.created_at.isoformat(),
            "finished_at": d["finished_at"] or
            (r.finished_at.isoformat() if r.finished_at else None)}


async def _rows_for_runs(s, runs: list[StudioRun]) -> dict[str, list]:
    """run_id -> [file, *children] in one query pair (no N+1)."""
    from app.models import FileRecord as _FR
    ids = [r.file_id for r in runs if r.file_id]
    if not ids:
        return {}
    rows = (await s.execute(
        select(_FR).where(or_(_FR.id.in_(ids), _FR.parent_file_id.in_(ids)))
    )).scalars().all()
    by_id = {f.id: f for f in rows}
    out: dict[str, list] = {}
    for r in runs:
        group = [by_id.get(r.file_id)] if r.file_id else []
        group += [f for f in rows if f.parent_file_id == r.file_id]
        out[r.id] = group
    return out


def _package_hash(package: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(package, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:16]


@router.post("/runs", status_code=202)
async def create_run(payload: RunRequest,
                     _: None = Depends(require_role("operator"))):
    """Run a sample against a skill version (drafts included, 图21 运行).

    The transaction is purpose=test with the click-time package frozen into
    the execution snapshot — later draft edits never rewrite run history.
    Sample originals are copied under the new transaction (lifecycle decoupled).
    Billing follows decision B3 (default: test runs bill like production)."""
    if not (1 <= len(payload.sample_ids) <= 10):
        raise HTTPException(400, detail={
            "code": "validation_failed",
            "message": "每次运行选择 1–10 个样本"})
    tenant = current_tenant()
    actor = current_actor()
    sf = session_factory()
    async with sf() as s:
        skill = await s.get(Skill, payload.skill_code)
        if skill is None or skill.tenant_id != tenant or skill.state != "active":
            raise HTTPException(404, detail={"code": "skill_not_found",
                                             "message": "技能不存在"})
        versions = (await s.execute(
            select(SkillVersion).where(
                SkillVersion.skill_code == payload.skill_code)
            .order_by(SkillVersion.version.desc()))).scalars().all()
        if payload.version is not None:
            ver = next((v for v in versions
                        if v.version == payload.version), None)
        else:
            ver = versions[0] if versions else None
        if ver is None:
            raise HTTPException(404, detail={"code": "version_not_found",
                                             "message": "技能版本不存在"})
        if not ver.package:
            raise HTTPException(409, detail={
                "code": "validation_failed",
                "message": "该版本还没有可运行的内容"})
        pkg = SkillPackageLoose(**ver.package)
        if (getattr(pkg, "processing_mode", "balanced") == "fast"
                and getattr(pkg, "skill_mode", "standard") == "advanced"):
            raise HTTPException(422, detail={
                "code": "validation_failed",
                "message": "极速模式不支持高级提取，请先切换为标准提取"})

        samples = []
        for sid in payload.sample_ids:
            row = await s.get(StudioSample, sid)
            if row is None or row.tenant_id != tenant:
                raise HTTPException(404, detail={"code": "sample_not_found",
                                                 "message": f"样本不存在: {sid}"})
            samples.append(row)

        from app import references
        txn = Transaction(tenant_id=tenant, skill_code=payload.skill_code,
                          skill_version=ver.version, purpose="test",
                          initiator_type="user", initiator_id=actor["name"],
                          initiator_label=actor["name"],
                          initiator_user_id=actor.get("user_id"))
        s.add(txn)
        await s.flush()
        try:
            txn.execution_snapshot = await references.build_execution_snapshot(
                s, tenant, payload.skill_code, ver.version)
        except references.ReferenceUnavailable as e:
            raise HTTPException(409, detail={
                "code": "reference_unavailable",
                "message": f"技能引用的依赖不存在：{e.skill_code}"
                           + (f" v{e.version}" if e.version else "")})
        ph = _package_hash(ver.package)

        runs: list[dict] = []
        st = get_storage()
        for sample in samples:
            blob = st.read_bytes(sample.storage_key)
            file_key = st.put_bytes(
                f"files/{tenant}/{txn.id}/{sample.file_name}", blob)
            f = FileRecord(tenant_id=tenant, transaction_id=txn.id,
                           file_name=sample.file_name, storage_path=file_key,
                           status="queued")
            s.add(f)
            await s.flush()
            run = StudioRun(
                tenant_id=tenant, skill_code=payload.skill_code,
                skill_version=ver.version, sample_id=sample.id,
                transaction_id=txn.id, file_id=f.id, package_hash=ph,
                created_by=actor["name"], status="queued",
                processing_mode=getattr(pkg, "processing_mode", "balanced"))
            s.add(run)
            await s.flush()
            runs.append(_run_view(run, _derive_run(run, [f])))
        await s.commit()
    txn_id = txn.id
    runner.submit(txn_id)   # dispatch seam: sync call, inprocess/celery inside
    return {"transaction_id": txn_id, "runs": runs}


@router.get("/runs")
async def list_runs(skill_code: str | None = None, sample_id: str | None = None):
    """Playground run history (图21 运行历史): newest first, 200 max."""
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        q = (select(StudioRun).where(StudioRun.tenant_id == tenant, StudioRun.file_id.in_(select(FileRecord.id).where(visible_file_cond())))
             .order_by(StudioRun.created_at.desc()).limit(200))
        if skill_code:
            q = q.where(StudioRun.skill_code == skill_code)
        if sample_id:
            q = q.where(StudioRun.sample_id == sample_id)
        rows = (await s.execute(q)).scalars().all()
        grouped = await _rows_for_runs(s, rows)
    return {"runs": [_run_view(r, _derive_run(r, grouped.get(r.id, [])))
                     for r in rows]}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str):
    """Run detail (图22): version, mode, duration, transaction + file ids."""
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        r = await s.get(StudioRun, run_id)
        if r is None or r.tenant_id != tenant:
            raise HTTPException(404, detail={"code": "run_not_found",
                                             "message": "运行记录不存在"})
        await require_file(s, r.file_id)
        txn = await s.get(Transaction, r.transaction_id)
        grouped = await _rows_for_runs(s, [r])
        out = _run_view(r, _derive_run(r, grouped.get(r.id, [])))
        out["transaction_status"] = txn.status if txn else None
        out["transaction_error"] = None   # file-level errors live on files
    return out


@router.post("/naming-preview")
async def naming_preview(payload: dict):
    """Server-side naming preview (§WP6: the ONLY implementation — the editor
    calls this 300ms after the last keystroke).

    D4 (#13): besides the tokens the rule already uses, return
    `available_tokens` — every variable legal for the CURRENT action — so the
    editor can show clickable tags without inventing the list itself, and
    `doc_index` appears only for split.

    Sample values come from the SERVER: the sample's latest Playground run
    result when there is one, otherwise a `‹field›` placeholder (an empty
    render made it look like the rule was broken)."""
    from app.extraction import naming
    from app.config import get_settings
    pattern = str(payload.get("pattern") or "")
    action = str(payload.get("action") or "rename")
    errors = naming.validate_pattern(pattern)
    tokens = naming.tokens_of(pattern)
    normalized, appended = naming.ensure_extension(pattern)
    sample = payload.get("sample") or {}
    data = sample.get("data") or {}
    clean = {k: (v.get("$value") if isinstance(v, dict) else v)
             for k, v in data.items()} if isinstance(data, dict) else {}

    # —— which data fields exist (server composes the tag list) ——
    field_names: list[str] = []
    raw_fields = payload.get("fields")
    if isinstance(raw_fields, list):
        for f in raw_fields:
            name = f.get("name") if isinstance(f, dict) else f
            if isinstance(name, str) and name.strip():
                field_names.append(name.strip())
    elif payload.get("skill_code"):
        ver = payload.get("version")
        sf = session_factory()
        async with sf() as s:
            q = select(SkillVersion).where(
                SkillVersion.tenant_id == current_tenant(),
                SkillVersion.skill_code == str(payload["skill_code"]))
            q = q.where(SkillVersion.version == int(ver)) if ver else \
                q.order_by(SkillVersion.version.desc())
            row = (await s.execute(q)).scalars().first()
        if row is not None:
            raw = row.package or {}
            field_names = [f.get("name") for f in (raw.get("fields") or [])
                           if isinstance(f, dict) and f.get("name")]
            for cat in raw.get("categories") or []:
                field_names += [f.get("name") for f in (cat.get("fields") or [])
                                if isinstance(f, dict) and f.get("name")]
    seen: list[str] = []
    for n in field_names:
        if n not in seen:
            seen.append(n)

    # —— sample values: latest Playground result of this sample, else ‹field› ——
    sample_id = payload.get("sample_id")
    server_data: dict = {}
    if sample_id:
        sf = session_factory()
        async with sf() as s:
            run = (await s.execute(
                select(StudioRun)
                .where(StudioRun.file_id.in_(select(FileRecord.id).where(visible_file_cond())),
                       StudioRun.tenant_id == current_tenant(),
                       StudioRun.sample_id == str(sample_id),
                       StudioRun.transaction_id.is_not(None))
                .order_by(StudioRun.created_at.desc()))).scalars().first()
            if run is not None:
                f = (await s.execute(
                    select(FileRecord)
                    .where(FileRecord.id == run.file_id, visible_file_cond())
                    .order_by(FileRecord.created_at, FileRecord.id))
                ).scalars().first()
                children = (await s.execute(
                    select(FileRecord)
                    .where(FileRecord.parent_file_id == (f.id if f else ""))
                    .order_by(FileRecord.created_at))
                ).scalars().all() if f else []
                for row in ([f] if f else []) + list(children):
                    for k, v in (row.result or {}).items():
                        if isinstance(v, dict) and "$value" in v and k not in server_data:
                            server_data[k] = v.get("$value")
    merged = dict(server_data)
    merged.update(clean)                      # explicit sample values still win
    render_data = {}
    for n in seen:
        v = merged.get(n)
        render_data[n] = v if v not in (None, "") else f"‹{n}›"

    available = ["original_name", "original_ext", "date", "time", "doc_type"]
    if action == "split":
        available.append("doc_index")
    available += [f"data.{n}" for n in seen]
    preview, r_errs = naming.render(
        pattern,
        original_name=str(sample.get("original_name") or "sample.pdf"),
        original_ext=str(sample.get("original_ext") or ".pdf"),
        output_is_pdf=bool(payload.get("searchable_pdf")),
        doc_type=sample.get("doc_type"),
        doc_index=sample.get("doc_index"),
        data=render_data or clean,
        output_tz=get_settings().output_tz)
    return {"ok": not errors and not r_errs, "preview": preview,
            "appended_ext": appended, "normalized_pattern": normalized,
            "errors": errors + r_errs, "tokens": tokens,
            "available_tokens": available,
            "field_names": seen,
            "sample_source": "playground" if server_data else "placeholder"}
