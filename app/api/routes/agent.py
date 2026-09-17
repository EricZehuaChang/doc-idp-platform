"""Agent service endpoints (9.15 WP2, §3.6): connection test + the skill list
the Windows Agent shows its operator. Agent Keys reach these through their
whitelist (app/tenancy.py); a logged-in browser user may also call them.

Read-only and tenant-scoped. Skills listed = published + active, restricted
to the calling key's allowed_skill_codes when it has one.
"""
from fastapi import APIRouter
from sqlalchemy import select

from app.db import session_factory
from app.models import Skill, SkillVersion, Tenant
from app.tenancy import current_actor, current_tenant

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

# keep in step with the FastAPI app version (app.main)
SERVER_VERSION = "0.1.0"


@router.get("/ping")
async def ping():
    """连接测试: proves address + key + scope in one call. Never leaks key
    material; skill_count reflects what THIS credential may actually use."""
    actor = current_actor()
    tenant = current_tenant()
    allowed = actor.get("allowed_skill_codes")
    sf = session_factory()
    async with sf() as s:
        t = await s.get(Tenant, tenant)
        rows = (await s.execute(
            select(Skill.code).where(Skill.tenant_id == tenant,
                                     Skill.state == "active"))).scalars().all()
    usable = [c for c in rows if allowed is None or c in allowed]
    return {"key_name": actor["name"].split(":", 1)[-1]
            if actor["name"].startswith("apikey:") else actor["name"],
            "tenant_name": (t.name if t and t.name else tenant),
            "skill_count": len(usable),
            "server_version": SERVER_VERSION,
            "upload_max_mb": 50}


@router.get("/skills")
async def skills():
    """The submission menu for the agent UI: published + active skills within
    this key's scope. processing_mode/output come from the published package
    (v1 packages read as balanced/off through the v2 schema's defaults)."""
    tenant = current_tenant()
    actor = current_actor()
    allowed = actor.get("allowed_skill_codes")
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(Skill).where(Skill.tenant_id == tenant,
                                Skill.state == "active"))).scalars().all()
        pub = (await s.execute(
            select(SkillVersion.skill_code, SkillVersion.version,
                   SkillVersion.package)
            .where(SkillVersion.status == "published")
            .order_by(SkillVersion.version))).all()
        # keep only the highest published version per skill
        latest: dict[str, tuple[int, dict]] = {}
        for code, version, package in pub:
            latest[code] = (version, package or {})
        out = []
        for sk in rows:
            if sk.code not in latest or (allowed is not None and sk.code not in allowed):
                continue
            version, package = latest[sk.code]
            out.append({
                "skill_code": sk.code, "name": sk.name,
                "published_version": version,
                "processing_mode": package.get("processing_mode", "balanced"),
                "output_enabled": bool((package.get("output") or {}).get("enabled")),
            })
    return {"skills": out}
