"""Auth API (§11.9): local login issuing a session JWT, current-user probe,
and admin user management. SSO/OIDC joins in M3 (auth_provider field ready).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.auth import security
from app.db import session_factory
from app.models import AuditLog, User
from app.tenancy import current_actor, current_tenant, has_role

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)     # enterprise floor; policy knob later
    role: str = "operator"                  # viewer | operator | admin
    tenant_id: str | None = None            # default: creator's tenant


@router.post("/login")
async def login(body: LoginBody):
    sf = session_factory()
    async with sf() as s:
        user = (await s.execute(
            select(User).where(User.email == body.email, User.active))).scalar_one_or_none()
        # timing-safe-ish: verify against a dummy hash when the user is unknown
        # so the response time doesn't leak account existence
        ph = user.password_hash if user and user.password_hash else security.hash_password("!")
        ok = security.verify_password(body.password, ph) and user is not None \
            and user.password_hash is not None
        if not ok:
            raise HTTPException(401, "invalid email or password")
        token = security.create_session_token(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)
        s.add(AuditLog(tenant_id=user.tenant_id, actor=user.email, action="auth.login"))
        await s.commit()
    return {"access_token": token, "token_type": "bearer",
            "role": user.role, "tenant_id": user.tenant_id, "email": user.email}


@router.get("/me")
async def me():
    actor = current_actor()
    return {"email": actor["name"], "role": actor["role"], "tenant_id": current_tenant()}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate):
    if not has_role("admin"):
        raise HTTPException(403, "admin role required")
    if body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(400, "role must be viewer|operator|admin")
    tenant = body.tenant_id or current_tenant()
    sf = session_factory()
    async with sf() as s:
        dup = (await s.execute(
            select(User).where(User.email == body.email,
                               User.tenant_id == tenant))).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(409, "user already exists in this tenant")
        user = User(tenant_id=tenant, email=body.email, role=body.role,
                    password_hash=security.hash_password(body.password))
        s.add(user)
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="auth.user_created", detail={"email": body.email,
                                                           "role": body.role}))
        await s.commit()
        return {"id": user.id, "email": user.email, "role": user.role, "tenant_id": tenant}
