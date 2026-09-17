"""Personal agent keys (9.15 WP2, §3.6): any active logged-in user can mint
their own restricted agent key for the Windows Agent. Scopes are fixed by
role — viewer keys can read skills but can never submit. The full key is
returned exactly once (show-once credential pattern, same as the admin page).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import security
from app.config import get_settings
from app.db import session_factory
from app.models import ApiKey, AuditLog
from app.tenancy import current_actor, current_tenant, has_role

router = APIRouter(prefix="/api/v1/me", tags=["me"])


def _require_user() -> dict:
    """Auth-on only: auth-off (lite/dev) has no users, so there is no 'me'."""
    actor = current_actor()
    if get_settings().auth_mode != "on" or not actor.get("user_id"):
        raise HTTPException(401, "需要登录后才能管理个人 API Key")
    return actor


def _key_view(row: ApiKey) -> dict:
    return {"id": row.id, "name": row.name, "key_type": row.key_type,
            "key_prefix": row.prefix,
            "scopes": [x for x in (row.scopes or "").split(",") if x],
            "allowed_skill_codes": row.allowed_skill_codes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None}


class AgentKeyCreate(BaseModel):
    name: str
    allowed_skill_codes: list[str] | None = None   # None = all skills, [] = none


@router.get("/api-keys")
async def list_my_keys():
    actor = _require_user()
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(ApiKey).where(ApiKey.tenant_id == current_tenant(),
                                 ApiKey.owner_user_id == actor["user_id"],
                                 ApiKey.active)
            .order_by(ApiKey.created_at))).scalars().all()
    return {"keys": [_key_view(r) for r in rows]}


@router.post("/api-keys", status_code=201)
async def create_my_key(body: AgentKeyCreate):
    """Mint a personal agent key. Fixed scopes: operator+ get submit+read,
    viewer gets read-only (§3.7: viewer's key must never submit)."""
    actor = _require_user()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "请为 Key 填写一个名称")
    if len(name) > 100:
        raise HTTPException(400, "Key 名称不能超过 100 字")
    scopes = "skills:read" if not has_role("operator") else "process:write,skills:read"
    full_key, prefix, key_hash = security.generate_api_key()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        row = ApiKey(tenant_id=tenant, key_hash=key_hash, prefix=prefix, name=name,
                     scopes=scopes, key_type="agent", owner_user_id=actor["user_id"],
                     allowed_skill_codes=body.allowed_skill_codes)
        s.add(row)
        s.add(AuditLog(tenant_id=tenant, actor=actor["name"],
                       action="me.agent_key_created",
                       detail={"name": name, "prefix": prefix,
                               "scopes": scopes,
                               "skills": body.allowed_skill_codes}))
        await s.commit()
        return {"id": row.id, "name": row.name, "key_type": "agent",
                "key_prefix": prefix, "scopes": scopes.split(","),
                "allowed_skill_codes": row.allowed_skill_codes,
                "created_at": row.created_at.isoformat(),
                "last_used_at": None,
                "key": full_key}       # shown exactly once


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_my_key(key_id: str):
    actor = _require_user()
    sf = session_factory()
    async with sf() as s:
        row = await s.get(ApiKey, key_id)
        if row is None or row.tenant_id != current_tenant() \
                or row.owner_user_id != actor["user_id"]:
            raise HTTPException(404, "api key not found")
        row.active = False
        s.add(AuditLog(tenant_id=current_tenant(), actor=actor["name"],
                       action="me.agent_key_revoked",
                       detail={"name": row.name, "prefix": row.prefix}))
        await s.commit()
