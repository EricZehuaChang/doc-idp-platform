"""Explicit platform operations for moving a local account from the default tenant."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, insert, select, text, update

from app.auth.membership import lock_membership
from app.billing import engine as billing
from app.config import get_settings
from app.db import session_factory
from app.models import ApiKey, AuditLog, AuthToken, Tenant, User
from app.tenancy import current_actor, current_tenant


def is_transfer_admin() -> bool:
    actor = current_actor()
    return bool(actor.get("user_id") and actor.get("role") == "admin"
                and not actor.get("api_key_id") and current_tenant() == get_settings().default_tenant)


def _require_transfer_admin() -> None:
    if not is_transfer_admin():
        raise HTTPException(403, "仅默认租户的平台管理员可转移成员")


def can_transfer(user: User) -> bool:
    return (is_transfer_admin() and user.tenant_id == current_tenant()
            and user.id != current_actor()["user_id"] and not user.unlimited
            and user.role in {"viewer", "operator", "tenant_admin"}
            and user.auth_provider == "local" and user.email_verified and bool(user.password_hash))


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(_require_transfer_admin)])


class NewTenant(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


@router.get("/transfer-tenants")
async def transfer_tenants():
    async with session_factory()() as s:
        rows = (await s.execute(select(Tenant.id, Tenant.name).where(
            Tenant.id != current_tenant(), Tenant.status == "active").order_by(Tenant.id))).all()
    return [{"id": row.id, "name": row.name} for row in rows]


@router.post("/transfer-tenants", status_code=201)
async def register_transfer_tenant(body: NewTenant):
    if body.id == current_tenant():
        raise HTTPException(400, "默认租户不能作为转移目标")
    async with session_factory()() as s, s.begin():
        await lock_membership(s, body.id)
        if await s.get(Tenant, body.id) is not None:
            raise HTTPException(409, "租户标识已存在")
        s.add(Tenant(id=body.id, name=body.name))
        s.add(AuditLog(tenant_id=current_tenant(), actor=current_actor()["name"],
                       action="auth.transfer_tenant_registered", detail={"tenant_id": body.id}))
    return {"id": body.id, "name": body.name}


class TransferBody(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)


@router.post("/users/{user_id}/transfer")
async def transfer_user(user_id: str, body: TransferBody):
    source = current_tenant()
    dest = body.tenant_id
    if dest == source:
        raise HTTPException(400, "目标租户必须与默认租户不同")

    async with session_factory()() as s, s.begin():
        await lock_membership(s, dest)
        target = await s.scalar(select(Tenant).where(Tenant.id == dest).with_for_update())
        if target is None:
            raise HTTPException(404, "目标租户不存在")
        if target.status != "active":
            raise HTTPException(409, "目标租户未启用")
        user = await s.scalar(select(User).where(User.id == user_id,
                                                User.tenant_id == source).with_for_update())
        if user is None:
            raise HTTPException(404, "默认租户中找不到该成员")
        if not can_transfer(user):
            raise HTTPException(409, "仅可转移已激活的普通本地成员或租户管理员，且不能转移自己")

        # No user FKs point at this row. Preserve its identity and credentials,
        # replacing it under the destination's RLS scope in this same transaction.
        values = {col.key: getattr(user, col.key) for col in User.__table__.columns}
        values.update(tenant_id=dest, session_epoch=user.session_epoch + 1, api_grants=None)
        detail = {"user_id": user_id, "source_tenant_id": source, "destination_tenant_id": dest}
        audit = {"actor": current_actor()["name"], "action": "auth.user_transferred", "detail": detail}
        await s.execute(update(ApiKey).where(ApiKey.tenant_id == source,
                                             ApiKey.owner_user_id == user_id).values(active=False))
        await s.execute(delete(AuthToken).where(AuthToken.tenant_id == source, AuthToken.user_id == user_id))
        await s.execute(delete(User).where(User.id == user_id, User.tenant_id == source))
        await s.execute(insert(AuditLog).values(tenant_id=source, **audit))
        if s.get_bind().dialect.name == "postgresql":
            # after_begin pinned the source GUC; switch this transaction to
            # the authorized destination without disabling any RLS policy.
            await s.execute(text("SELECT set_config('app.tenant_id', :dest, true)"), {"dest": dest})
        if await s.scalar(select(User.id).where(User.tenant_id == dest, User.email == values["email"]).limit(1)):
            raise HTTPException(409, "目标租户已有相同邮箱的成员")
        if values["active"]:
            try:
                await billing.enforce_member_cap(s, dest)
            except billing.EntitlementExceeded as e:
                raise HTTPException(403, f"目标租户套餐({e.plan})成员数已达上限 {e.limit}") from e
        await s.execute(insert(User.__table__).values(**values))
        await s.execute(insert(AuditLog).values(tenant_id=dest, **audit))
    return {"id": user_id, "email": values["email"], "role": values["role"], "tenant_id": dest}
