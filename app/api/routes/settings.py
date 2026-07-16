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


# —— OIDC SSO config (§11.9): platform-level IdP binding ——

@router.get("/oidc")
async def get_oidc():
    from app.auth import oidc

    _require_admin()
    sf = session_factory()
    async with sf() as s:
        return oidc.public_view(await oidc.load_config(s))


class OidcBody(BaseModel):
    enabled: bool = True
    issuer: str
    client_id: str
    client_secret: str | None = None    # None = keep stored


@router.put("/oidc")
async def put_oidc(body: OidcBody):
    from app.auth import oidc as oidc_mod
    from app.auth import security as sec

    _require_admin()
    sf = session_factory()
    async with sf() as s:
        old = await oidc_mod.load_config(s) or {}
        cfg = {"enabled": body.enabled, "issuer": body.issuer.rstrip("/"),
               "client_id": body.client_id,
               "client_secret_enc": old.get("client_secret_enc", "")}
        if body.client_secret:
            cfg["client_secret_enc"] = sec.encrypt_value(body.client_secret)
        row = await s.get(PlatformSetting, oidc_mod.SETTING_KEY)
        if row is None:
            s.add(PlatformSetting(key=oidc_mod.SETTING_KEY, value=cfg))
        else:
            row.value = cfg
        s.add(AuditLog(tenant_id=current_tenant(), actor=current_actor()["name"],
                       action="settings.oidc_updated", detail={"issuer": cfg["issuer"]}))
        await s.commit()
        return oidc_mod.public_view(cfg)


# —— tenant BYOK provider keys (§11.10): bring-your-own model key per tenant ——

@router.get("/providers")
async def list_providers():
    """Provider inventory for the BYOK page: platform key presence (env) and
    whether this tenant has brought its own. Never returns key material."""
    import os

    from app.config import load_providers
    from app.extraction import byok

    _require_admin()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        await byok.warm(s, tenant, force=True)
    cfg = load_providers()
    out = []
    for name, p in cfg["providers"].items():
        out.append({"name": name, "model": p.model, "base_url": p.base_url,
                    "active": name == cfg["active"],
                    "platform_key": bool(p.api_key_env and os.environ.get(p.api_key_env)),
                    "byok_set": byok.has_key(tenant, name)})
    return {"providers": out}


class ByokBody(BaseModel):
    api_key: str


@router.put("/providers/{name}/key")
async def put_byok(name: str, body: ByokBody):
    from app.config import load_providers
    from app.extraction import byok

    _require_admin()
    if name not in load_providers()["providers"]:
        raise HTTPException(404, f"unknown provider: {name}")
    if not body.api_key.strip():
        raise HTTPException(400, "api_key 不能为空")
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        await byok.put(s, tenant, name, body.api_key.strip())
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="settings.byok_set", detail={"provider": name}))
        await s.commit()
    return {"provider": name, "byok_set": True}


@router.delete("/providers/{name}/key")
async def delete_byok(name: str):
    from app.extraction import byok

    _require_admin()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        await byok.remove(s, tenant, name)
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="settings.byok_removed", detail={"provider": name}))
        await s.commit()
    return {"provider": name, "byok_set": False}


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
