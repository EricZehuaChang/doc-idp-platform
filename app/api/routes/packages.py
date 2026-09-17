"""9.15 WP7 (R03/R04): encrypted skill-package export + two-step import.

Roles (§3.7): operator and above; viewer and agent keys are rejected by the
role dependency. Secrets: the passphrase exists only in the export response
(Cache-Control: no-store); stashed import payloads are re-encrypted at rest
with the platform key and bound to tenant+user with a 30-minute TTL.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.auth.security import encrypt_value, decrypt_value
from app.db import session_factory
from app.extraction.naming import sanitize
from app.models import AuditLog, Skill, SkillVersion
from app.skillengine.package import (PackageError, build_package,
                                     generate_passphrase, read_package)
from app.skillengine.schema import SkillPackageLoose
from app.skillengine.validation import validate_package
from app.storage import get_storage
from app.tenancy import current_actor, current_tenant, require_role

router = APIRouter(prefix="/api/v1/skill-packages", tags=["skill-packages"])

_STASH_TTL = timedelta(minutes=30)


def _collect_requirements(pkg: SkillPackageLoose) -> dict:
    """Model channel names used by the package (§3.8 requirements). Parsers
    are not user-selectable in this build, so the list stays empty unless a
    future DSL version adds parser pinning."""
    channels = []
    mb = pkg.model_binding
    mb = mb if isinstance(mb, dict) else (mb.model_dump() if mb else {})
    for c in (mb.get("extractor"), mb.get("fallback"), mb.get("challenger")):
        if c and c not in channels:
            channels.append(c)
    return {"channels": channels, "parsers": []}


async def _find_version(s, tenant: str, code: str, version: int):
    res = await s.execute(
        select(SkillVersion).where(SkillVersion.skill_code == code,
                                   SkillVersion.tenant_id == tenant,
                                   SkillVersion.version == version))
    return res.scalar_one_or_none()


@router.post("/export", dependencies=[Depends(require_role("operator"))])
async def export_package(payload: dict):
    tenant = current_tenant()
    code = str(payload.get("skill_code") or "")
    version = payload.get("version")
    sf = session_factory()
    async with sf() as s:
        row = (await _find_version(s, tenant, code, int(version))) \
            if version is not None else None
        if row is None:
            raise HTTPException(404, "version not found")
        pkg_raw = row.package or {}
        pkg = SkillPackageLoose(**pkg_raw)
        dependencies: dict = {}
        if pkg.skill_mode == "advanced":
            for cat in pkg.categories or []:
                ref = cat.skill_ref if hasattr(cat, "skill_ref") else \
                    (cat.get("skill_ref") if isinstance(cat, dict) else None)
                if not ref:
                    continue
                ref = ref if isinstance(ref, dict) else ref.model_dump()
                ref_code, ref_ver = ref.get("skill_code"), ref.get("version")
                if not ref_code:
                    continue
                if ref_ver:
                    dep_row = await _find_version(s, tenant, ref_code,
                                                  int(ref_ver))
                else:                    # draft follows latest published
                    res = await s.execute(
                        select(SkillVersion).where(
                            SkillVersion.skill_code == ref_code,
                            SkillVersion.tenant_id == tenant,
                            SkillVersion.status == "published")
                        .order_by(SkillVersion.version.desc()))
                    dep_row = res.scalars().first()
                    ref_ver = dep_row.version if dep_row else None
                dependencies[cat.id] = {
                    "skill_code": ref_code, "version": ref_ver,
                    "package": dep_row.package if dep_row else None}
        inner_skill = {
            "package": pkg_raw, "skill_code": code, "name": pkg.name,
            "kind": pkg.kind, "version": row.version,
            "status": row.status, "changelog": row.changelog or "",
        }
    passphrase = generate_passphrase()
    blob = build_package(inner_skill, dependencies,
                         _collect_requirements(pkg),
                         int(pkg_raw.get("schema_version") or 2), passphrase)
    if len(blob) > 5 * 1024 * 1024:
        raise HTTPException(413, "包超过 5 MB：请减少金样本或拆分技能")
    import hashlib
    sha = hashlib.sha256(blob).hexdigest()
    label = f"v{row.version}" + ("" if row.status == "published" else "-draft")
    file_name = f"{sanitize(pkg.name)}_{label}_" \
                f"{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d-%H%M%S')}.zip"
    actor = current_actor()
    async with sf() as s:
        s.add(AuditLog(tenant_id=tenant, actor=actor.get("name") or "api",
                       action="skills.package_exported",
                       detail={"skill_code": code, "version": row.version,
                               "sha256": sha}))
        await s.commit()
    from fastapi import Response
    return Response(content=json.dumps({
        "file_name": file_name, "content_base64": base64.b64encode(blob).decode(),
        "passphrase": passphrase, "sha256": sha, "size": len(blob)}),
        media_type="application/json",
        headers={"Cache-Control": "no-store"})


def _stash(tenant: str, user: str, inner: dict) -> str:
    """§3.8: stash the decrypted inner JSON (platform-key encrypted) under an
    import token, bound to tenant+user, 30-minute TTL. The expiry rides in
    the blob — no extra table, and a missing key is just an expired import."""
    token = secrets.token_urlsafe(16)
    blob = json.dumps({
        "tenant": tenant, "user": user,
        "expires_at": (datetime.now(timezone.utc) + _STASH_TTL).isoformat(),
        "inner": inner}).encode()
    get_storage().put_bytes(f"imports/{tenant}/{token}.bin",
                            encrypt_value(blob.decode()).encode())
    return token


def _unstash(tenant: str, user: str, token: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return None
    st = get_storage()
    key = f"imports/{tenant}/{token}.bin"
    if not st.exists(key):
        return None
    try:
        blob = json.loads(decrypt_value(st.read_bytes(key).decode()))
    except Exception:
        return None
    if blob.get("tenant") != tenant or blob.get("user") != user:
        return None
    if datetime.fromisoformat(blob["expires_at"]) < datetime.now(timezone.utc):
        st.delete(key)
        return None
    return blob.get("inner")


def _unstash_delete(tenant: str, token: str) -> None:
    try:
        get_storage().delete(f"imports/{tenant}/{token}.bin")
    except Exception:
        pass


def _model_channels() -> list[str]:
    """Channel names configured in this environment (for missing-dep mapping):
    platform providers.yaml plus tenant custom channels (names only — keys
    and base URLs never enter the preview payload, §3.8)."""
    from app.config import load_providers
    try:
        names = list(load_providers()["providers"].keys())
    except Exception:
        names = []
    try:
        from app.extraction import custom_providers
        names += list(custom_providers.names(current_tenant()))
    except Exception:
        pass
    return names


@router.post("/import/preview",
             dependencies=[Depends(require_role("operator"))])
async def import_preview(zip_file: UploadFile = File(...),
                         passphrase: str = Form(...)):
    """Step 1: decrypt + validate, report conflicts/dependencies, stash."""
    tenant = current_tenant()
    user = current_actor().get("name") or "api"
    raw = await zip_file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "包超过 5 MB")
    try:
        inner, sha = read_package(raw, passphrase)
    except PackageError:
        raise HTTPException(422, detail={"code": "package_decrypt_failed",
                                         "message": "口令错误或文件已损坏"})
    skill = inner.get("skill") or {}
    pkg_raw = skill.get("package") or {}
    try:
        pkg = SkillPackageLoose(**pkg_raw)
    except Exception:
        raise HTTPException(422, detail={"code": "package_invalid",
                                         "message": "包内技能定义不合法"})
    errors, _warnings = validate_package(pkg)
    if errors:
        raise HTTPException(422, detail={"code": "validation_failed",
                                         "message": "技能包校验失败",
                                         "errors": errors})

    sf = session_factory()
    async with sf() as s:
        # same-name conflicts: case-insensitive, trimmed; deleted ones are
        # listed but marked "需先恢复" and cannot be overwrite targets (§WP7)
        rows = (await s.execute(
            select(Skill).where(Skill.tenant_id == tenant))).scalars().all()
        wanted = (skill.get("name") or "").strip().lower()
        conflicts = [{"code": k.code, "name": k.name, "state": k.state,
                      "recoverable": k.state == "deleted"}
                     for k in rows if (k.name or "").strip().lower() == wanted]
        available_channels = _model_channels()
    missing_channels = [c for c in inner.get("requirements", {}).get("channels", [])
                        if c not in available_channels]
    references = []
    for cat in pkg.categories or []:
        ref = cat.skill_ref
        ref = ref if isinstance(ref, dict) else             (ref.model_dump() if ref else None)
        if ref and ref.get("skill_code"):
            references.append({"category_id": cat.id,
                               "doc_type": cat.doc_type,
                               "skill_code": ref.get("skill_code"),
                               "version": ref.get("version")})
    token = _stash(tenant, user, inner)
    return {"import_token": token, "sha256": sha,
            "skill": {"code": skill.get("skill_code"), "name": skill.get("name"),
                      "kind": skill.get("kind"), "version": skill.get("version"),
                      "status": skill.get("status"),
                      "mode": pkg.skill_mode,
                      "processing_mode": pkg.processing_mode,
                      "field_count": len(pkg.fields),
                      "category_count": len(pkg.categories or [])},
            "conflicts": conflicts,
            "missing_channels": missing_channels,
            "available_channels": available_channels,
            "references": references}


def _suggest_code(base: str) -> str:
    clean = re.sub(r"[^a-z0-9_]", "_", (base or "imported").lower())[:24] or "imported"
    return f"{clean}_{secrets.token_hex(3)}"


@router.post("/import/commit",
             dependencies=[Depends(require_role("operator"))])
async def import_commit(payload: dict):
    """Step 2: apply mappings + conflict decision; everything in ONE DB
    transaction — on failure nothing is created (§WP7)."""
    tenant = current_tenant()
    user = current_actor().get("name") or "api"
    inner = _unstash(tenant, user, payload.get("import_token") or "")
    if inner is None:
        raise HTTPException(410, detail={"code": "import_token_expired",
                                         "message": "导入会话已过期，请重新解析"})
    skill = inner.get("skill") or {}
    pkg_raw = json.loads(json.dumps(skill.get("package") or {}))
    deps = inner.get("dependencies") or {}
    channel_map: dict = payload.get("channel_map") or {}
    ref_map: dict = payload.get("ref_map") or {}      # cat_id -> "inline"|"skill:CODE"
    conflict: str = payload.get("conflict") or "rename"
    new_code = (payload.get("new_code") or "").strip()
    new_name = (payload.get("new_name") or "").strip()
    overwrite_target = (payload.get("overwrite_target") or "").strip()

    sf = session_factory()
    async with sf() as s:
        # —— channel mapping ——
        if channel_map:
            mb = pkg_raw.get("model_binding") or {}
            for k in ("extractor", "fallback", "challenger"):
                if mb.get(k) in channel_map:
                    mb[k] = channel_map[mb[k]] or ""
            pkg_raw["model_binding"] = mb

        # —— reference decisions ——
        changelog_extra = []
        for cat in pkg_raw.get("categories") or []:
            ref = cat.get("skill_ref") or {}
            if not ref.get("skill_code"):
                continue
            decision = ref_map.get(cat.get("id"), "inline")
            dep_pkg = (deps.get(cat.get("id")) or {}).get("package") or {}
            if decision == "inline":
                dep = dep_pkg or {}
                cat["fields"] = list(cat.get("fields") or []) + \
                    list(dep.get("fields") or [])
                cat["validators"] = list(cat.get("validators") or []) + \
                    list(dep.get("validators") or [])
                cat["additional_rules"] = "；".join(x for x in (
                    cat.get("additional_rules") or "",
                    dep.get("additional_rules") or "") if x)
                cat["handler"] = "inline"
                cat["skill_ref"] = None
                cat["output_shape"] = dep.get("output_shape") or \
                    cat.get("output_shape") or "object"
                changelog_extra.append(f"类别「{cat.get('doc_type')}」引用转内联")
            elif decision.startswith("skill:"):
                target = decision.split(":", 1)[1]
                tgt = (await s.execute(
                    select(Skill).where(Skill.tenant_id == tenant,
                                        Skill.code == target))).scalar_one_or_none()
                if tgt is None or tgt.state == "deleted":
                    raise HTTPException(422, detail={
                        "code": "reference_target_invalid",
                        "message": f"引用目标 {target} 不可用"})
                cat["skill_ref"] = {"skill_code": target, "version": None}
                changelog_extra.append(f"类别「{cat.get('doc_type')}」映射到 {target}")
        pkg = SkillPackageLoose(**pkg_raw)
        errors, _warnings = validate_package(pkg)
        if errors:
            raise HTTPException(422, detail={"code": "validation_failed",
                                             "message": "映射后校验失败",
                                             "errors": errors})

        source_name = skill.get("name") or pkg.name
        source_ver = skill.get("version")
        changelog = (f"从技能包导入：{source_name} v{source_ver}"
                     f"（{(payload.get('sha256') or '')[:8]}）")
        if changelog_extra:
            changelog += "；" + "；".join(changelog_extra)

        from app.api.routes.skills import _enforce_skill_seat
        if conflict == "overwrite":
            if not overwrite_target:
                raise HTTPException(422, "overwrite requires a target skill")
            tgt = (await s.execute(
                select(Skill).where(Skill.tenant_id == tenant,
                                    Skill.code == overwrite_target))).scalar_one_or_none()
            if tgt is None or tgt.state == "deleted":
                raise HTTPException(422, "overwrite target not available")
            next_v = 1 + (await s.execute(
                select(SkillVersion.version).where(
                    SkillVersion.tenant_id == tenant,
                    SkillVersion.skill_code == tgt.code)
                .order_by(SkillVersion.version.desc()))).scalars().first()
            pkg_raw["skill_code"] = tgt.code
            s.add(SkillVersion(tenant_id=tenant, skill_code=tgt.code,
                               version=next_v, status="draft",
                               package=pkg_raw, changelog=changelog))
            new_code, version = tgt.code, next_v
        else:
            code = new_code or _suggest_code(skill.get("skill_code") or "imported")
            existing = (await s.execute(
                select(Skill).where(Skill.tenant_id == tenant,
                                    Skill.code == code))).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(409, f"skill exists: {code}")
            name = new_name or f"{source_name}（导入）"
            await _enforce_skill_seat(s, tenant)   # plan skill cap (§12.3)
            s.add(Skill(code=code, tenant_id=tenant, name=name,
                        kind=skill.get("kind") or "extract"))
            pkg_raw["skill_code"] = code
            pkg_raw["name"] = name
            s.add(SkillVersion(tenant_id=tenant, skill_code=code, version=1,
                               status="draft", package=pkg_raw,
                               changelog=changelog))
            new_code, version = code, 1
        s.add(AuditLog(tenant_id=tenant, actor=user,
                       action="skills.package_imported",
                       detail={"skill_code": new_code, "version": version,
                               "sha256": (payload.get("sha256") or "")[:64],
                               "conflict": conflict}))
        await s.commit()          # single transaction: fail => nothing created
    _unstash_delete(tenant, payload.get("import_token") or "")
    return {"skill_code": new_code, "version": version, "status": "draft"}
