"""Platform settings API (design v0.2 §11.8): SMTP config CRUD + test send.
Admin-only. The SMTP password is write-only: PUT stores it encrypted, GET
returns has_password instead of any echo.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from app.db import session_factory
from app.models import AuditLog, PlatformSetting
from app.notify import mailer
from app.tenancy import current_actor, current_tenant, has_role

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _require_admin() -> None:
    if not has_role("admin"):
        raise HTTPException(403, "admin role required")


@router.get("/smtp")
async def get_smtp():
    _require_admin()
    sf = session_factory()
    async with sf() as s:
        return mailer.public_view(await mailer.load_config(s))


class SmtpBody(BaseModel):
    host: str
    port: int = 587
    security: str = "starttls"          # ssl | starttls | none
    username: str = ""
    password: str | None = None         # None = keep the stored one
    from_addr: str
    from_name: str = ""
    reply_to: str = ""


@router.put("/smtp")
async def put_smtp(body: SmtpBody):
    _require_admin()
    if body.security not in ("ssl", "starttls", "none"):
        raise HTTPException(400, "security must be ssl|starttls|none")
    sf = session_factory()
    async with sf() as s:
        old = await mailer.load_config(s) or {}
        cfg = {"host": body.host, "port": body.port, "security": body.security,
               "username": body.username, "from_addr": body.from_addr,
               "from_name": body.from_name, "reply_to": body.reply_to,
               "password_enc": old.get("password_enc", "")}
        if body.password:                       # write-only: only overwrite when provided
            cfg["password_enc"] = mailer.encrypt_password(body.password)
        row = await s.get(PlatformSetting, mailer.SETTING_KEY)
        if row is None:
            s.add(PlatformSetting(key=mailer.SETTING_KEY, value=cfg))
        else:
            row.value = cfg
        s.add(AuditLog(tenant_id=current_tenant(), actor=current_actor()["name"],
                       action="settings.smtp_updated", detail={"host": body.host}))
        await s.commit()
        return mailer.public_view(cfg)


class SmtpTestBody(BaseModel):
    to: EmailStr


@router.post("/smtp/test")
async def test_smtp(body: SmtpTestBody):
    """Live connectivity check (§11.8: never let an admin save a dead relay
    without knowing). Sends a real mail; failures surface as 502 with cause."""
    _require_admin()
    sf = session_factory()
    async with sf() as s:
        try:
            await mailer.send(s, body.to, "SMTP 测试邮件",
                              "这是一封来自文档识别平台的测试邮件。收到即代表发信配置可用。")
        except mailer.MailerNotConfigured:
            raise HTTPException(409, "SMTP 未配置")
        except Exception as e:
            raise HTTPException(502, f"发送失败：{e}")
    return {"status": "sent", "to": body.to}
