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
            and user.password_hash is not None and user.email_verified
        if not ok:
            raise HTTPException(401, "invalid email or password")
        token = security.create_session_token(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)
        s.add(AuditLog(tenant_id=user.tenant_id, actor=user.email, action="auth.login"))
        await s.commit()
    return {"access_token": token, "token_type": "bearer",
            "role": user.role, "tenant_id": user.tenant_id, "email": user.email,
            "must_change_password": user.must_change_password}


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
        # admin activation mode (§11.8): initial password + forced change on
        # first login — the SMTP-less path for air-gapped deployments
        user = User(tenant_id=tenant, email=body.email, role=body.role,
                    password_hash=security.hash_password(body.password),
                    must_change_password=True)
        s.add(user)
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="auth.user_created", detail={"email": body.email,
                                                           "role": body.role}))
        await s.commit()
        return {"id": user.id, "email": user.email, "role": user.role, "tenant_id": tenant}


class InviteBody(BaseModel):
    email: EmailStr
    role: str = "operator"


@router.post("/invite", status_code=201)
async def invite(body: InviteBody):
    """Email-verification mode (§11.8): create a dormant account and mail a
    one-time activation link. 409 when SMTP is unconfigured — the UI then
    steers the admin to direct creation (admin activation mode)."""
    from app.auth import tokens
    from app.notify import mailer

    if not has_role("admin"):
        raise HTTPException(403, "admin role required")
    if body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(400, "role must be viewer|operator|admin")
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        if await mailer.load_config(s) is None:
            raise HTTPException(409, "SMTP 未配置：请先在 设置→邮件 配置发信，或改用直接建号")
        existing = (await s.execute(
            select(User).where(User.email == body.email,
                               User.tenant_id == tenant))).scalar_one_or_none()
        if existing is not None and existing.email_verified:
            raise HTTPException(409, "user already exists in this tenant")
        user = existing or User(tenant_id=tenant, email=body.email, role=body.role,
                                email_verified=False, password_hash=None)
        if existing is None:
            s.add(user)
            await s.flush()
        try:
            token = await tokens.issue(s, user, "invite")
        except tokens.RateLimited as e:
            raise HTTPException(429, str(e))
        await mailer.send(
            s, user.email, "您被邀请加入文档识别平台",
            "请打开以下链接设置密码并激活账号（24 小时内有效，仅可使用一次）：\n"
            f"{_frontend_base()}/#/activate?token={token}")
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="auth.invite_sent", detail={"email": user.email}))
        await s.commit()
    return {"email": user.email, "status": "invited"}


class ActivateBody(BaseModel):
    token: str
    password: str = Field(min_length=8)


@router.post("/activate")
async def activate(body: ActivateBody):
    """Invited user sets the initial password; token burns in the same tx."""
    from app.auth import tokens

    sf = session_factory()
    async with sf() as s:
        user = await tokens.consume(s, body.token, "invite")
        if user is None:
            raise HTTPException(400, "链接无效或已过期，请联系管理员重新邀请")
        user.password_hash = security.hash_password(body.password)
        user.email_verified = True
        user.must_change_password = False
        s.add(AuditLog(tenant_id=user.tenant_id, actor=user.email, action="auth.activated"))
        await s.commit()
    return {"email": user.email, "status": "active"}


class ForgotBody(BaseModel):
    email: EmailStr


@router.post("/forgot")
async def forgot_password(body: ForgotBody):
    """Password reset request. Always 200 with the same body — the response
    must not disclose whether the account exists (§11.8 anti-enumeration);
    rate limiting still applies to real accounts (429 leaks nothing new to the
    owner of the mailbox)."""
    from app.auth import tokens
    from app.notify import mailer

    sf = session_factory()
    async with sf() as s:
        user = (await s.execute(
            select(User).where(User.email == body.email, User.active,
                               User.email_verified))).scalar_one_or_none()
        if user is not None:
            if await mailer.load_config(s) is None:
                raise HTTPException(409, "SMTP 未配置，无法发送重置邮件：请联系管理员重置密码")
            try:
                token = await tokens.issue(s, user, "reset")
            except tokens.RateLimited as e:
                raise HTTPException(429, str(e))
            await mailer.send(
                s, user.email, "重置您的密码",
                "请打开以下链接设置新密码（24 小时内有效，仅可使用一次）：\n"
                f"{_frontend_base()}/#/reset?token={token}")
            await s.commit()
    return {"status": "ok", "message": "若该邮箱存在账号，重置邮件已发送"}


class ResetBody(BaseModel):
    token: str
    password: str = Field(min_length=8)


@router.post("/reset")
async def reset_password(body: ResetBody):
    from app.auth import tokens

    sf = session_factory()
    async with sf() as s:
        user = await tokens.consume(s, body.token, "reset")
        if user is None:
            raise HTTPException(400, "链接无效或已过期，请重新发起找回")
        user.password_hash = security.hash_password(body.password)
        user.must_change_password = False
        s.add(AuditLog(tenant_id=user.tenant_id, actor=user.email, action="auth.password_reset"))
        await s.commit()
    return {"email": user.email, "status": "ok"}


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


@router.post("/change-password")
async def change_password(body: ChangePasswordBody):
    """Logged-in self-service change; also clears the first-login force flag."""
    actor = current_actor()
    if not actor.get("user_id"):
        raise HTTPException(400, "当前会话没有用户身份（API Key / 免登录模式不支持改密）")
    sf = session_factory()
    async with sf() as s:
        user = await s.get(User, actor["user_id"])
        if user is None or not user.password_hash \
                or not security.verify_password(body.old_password, user.password_hash):
            raise HTTPException(401, "原密码不正确")
        user.password_hash = security.hash_password(body.new_password)
        user.must_change_password = False
        s.add(AuditLog(tenant_id=user.tenant_id, actor=user.email,
                       action="auth.password_changed"))
        await s.commit()
    return {"status": "ok"}


def _frontend_base() -> str:
    """Base URL used inside emails. Configurable for real deployments; the
    default matches the single-process private deploy (app serves webdist)."""
    import os
    return os.environ.get("IDP_PUBLIC_URL", "http://127.0.0.1:8200").rstrip("/")
