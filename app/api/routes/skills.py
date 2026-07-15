"""Skills API (design v0.2 §7 group 2): CRUD + versioning. M1 covers create /
list / get / new draft version / publish — enough for the self-serve loop via
API; Studio UI arrives in M2 on top of these same endpoints (API-first).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db import session_factory
from app.models import Skill, SkillVersion
from app.skillengine.schema import SkillPackage
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


class SkillCreate(BaseModel):
    package: SkillPackage
    changelog: str = ""


@router.post("", status_code=201)
async def create_skill(payload: SkillCreate):
    tenant = current_tenant()
    pkg = payload.package
    sf = session_factory()
    async with sf() as s:
        if await s.get(Skill, pkg.skill_code) is not None:
            raise HTTPException(409, f"skill exists: {pkg.skill_code}")
        s.add(Skill(code=pkg.skill_code, tenant_id=tenant,
                    name=pkg.name or pkg.skill_code, kind=pkg.kind))
        s.add(SkillVersion(tenant_id=tenant, skill_code=pkg.skill_code, version=1,
                           status="draft", package=pkg.model_dump(),
                           changelog=payload.changelog or "initial draft"))
        await s.commit()
    return {"skill_code": pkg.skill_code, "version": 1, "status": "draft"}


@router.get("")
async def list_skills():
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(Skill).where(Skill.tenant_id == tenant,
                                Skill.state != "deleted"))).scalars().all()
        return [{"skill_code": r.code, "name": r.name, "kind": r.kind, "state": r.state}
                for r in rows]


@router.get("/{skill_code}")
async def get_skill(skill_code: str):
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        skill = await s.get(Skill, skill_code)
        if skill is None or skill.tenant_id != tenant:
            raise HTTPException(404, "skill not found")
        versions = (await s.execute(
            select(SkillVersion).where(SkillVersion.skill_code == skill_code)
            .order_by(SkillVersion.version))).scalars().all()
        return {"skill_code": skill.code, "name": skill.name, "kind": skill.kind,
                "state": skill.state,
                "versions": [{"version": v.version, "status": v.status,
                              "changelog": v.changelog} for v in versions],
                "latest_package": versions[-1].package if versions else None}


class DraftUpdate(BaseModel):
    package: SkillPackage
    changelog: str = ""


@router.post("/{skill_code}/versions", status_code=201)
async def new_draft(skill_code: str, payload: DraftUpdate):
    """Published versions are immutable (§5.1) — any change opens a new draft."""
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        skill = await s.get(Skill, skill_code)
        if skill is None or skill.tenant_id != tenant:
            raise HTTPException(404, "skill not found")
        latest = (await s.execute(
            select(SkillVersion).where(SkillVersion.skill_code == skill_code)
            .order_by(SkillVersion.version.desc()))).scalars().first()
        next_ver = (latest.version + 1) if latest else 1
        s.add(SkillVersion(tenant_id=tenant, skill_code=skill_code, version=next_ver,
                           status="draft", package=payload.package.model_dump(),
                           changelog=payload.changelog))
        await s.commit()
    return {"skill_code": skill_code, "version": next_ver, "status": "draft"}


@router.post("/{skill_code}/versions/{version}/publish")
async def publish(skill_code: str, version: int):
    """Publish draft; previous published version is archived (rollback = republish it).
    TODO(M2): golden-set diff report gate before publish (PM item #4)."""
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        target = (await s.execute(
            select(SkillVersion).where(SkillVersion.skill_code == skill_code,
                                       SkillVersion.version == version))).scalar_one_or_none()
        if target is None or target.tenant_id != tenant:
            raise HTTPException(404, "version not found")
        if target.status == "published":
            return {"skill_code": skill_code, "version": version, "status": "published"}
        current = (await s.execute(
            select(SkillVersion).where(SkillVersion.skill_code == skill_code,
                                       SkillVersion.status == "published"))).scalars().all()
        for c in current:
            c.status = "archived"
        target.status = "published"
        await s.commit()
    return {"skill_code": skill_code, "version": version, "status": "published"}
