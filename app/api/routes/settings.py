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


# —— tenant API keys (API-first §7): mint/list/revoke integration credentials ——

@router.get("/api-keys")
async def list_api_keys():
    from sqlalchemy import select

    from app.models import ApiKey

    _require_admin()
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(ApiKey).where(ApiKey.tenant_id == current_tenant())
            .order_by(ApiKey.created_at))).scalars().all()
        return [{"id": k.id, "name": k.name, "prefix": k.prefix, "active": k.active,
                 "scopes": k.scopes, "quota_mode": k.quota_mode,
                 "allocated_balance": round(k.allocated_balance or 0.0, 4),
                 "allocated_frozen": round(k.allocated_frozen or 0.0, 4),
                 "created_at": k.created_at.isoformat() if k.created_at else None}
                for k in rows]


class ApiKeyCreate(BaseModel):
    name: str = ""


@router.post("/api-keys", status_code=201)
async def create_api_key(body: ApiKeyCreate):
    """Mint a tenant API key. The FULL key appears in this response only —
    the DB stores prefix + sha256 (standard show-once credential pattern)."""
    from app.auth import security
    from app.models import ApiKey

    _require_admin()
    full_key, prefix, key_hash = security.generate_api_key()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        row = ApiKey(tenant_id=tenant, key_hash=key_hash, prefix=prefix,
                     name=body.name.strip() or "unnamed")
        s.add(row)
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="settings.api_key_created",
                       detail={"name": row.name, "prefix": prefix}))
        await s.commit()
        return {"id": row.id, "name": row.name, "prefix": prefix,
                "api_key": full_key}


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str):
    """Revoke (deactivate) — takes effect on the key's next request."""
    from app.models import ApiKey

    _require_admin()
    sf = session_factory()
    async with sf() as s:
        row = await s.get(ApiKey, key_id)
        if row is None or row.tenant_id != current_tenant():
            raise HTTPException(404, "api key not found")
        row.active = False
        s.add(AuditLog(tenant_id=row.tenant_id, actor=current_actor()["name"],
                       action="settings.api_key_revoked",
                       detail={"name": row.name, "prefix": row.prefix}))
        await s.commit()
    return {"id": key_id, "active": False}


class KeyQuotaBody(BaseModel):
    mode: str                               # pool | allocated (§12.7)


@router.put("/api-keys/{key_id}/quota")
async def set_key_quota_mode(key_id: str, body: KeyQuotaBody):
    """Switch a key between shared-pool and allocated-budget billing (§12.7).
    Switching back to pool keeps any allocated remainder parked on the key —
    reclaim it explicitly so the money trail stays in the ledger."""
    from app.models import ApiKey

    _require_admin()
    if body.mode not in ("pool", "allocated"):
        raise HTTPException(400, "mode must be pool|allocated")
    sf = session_factory()
    async with sf() as s:
        row = await s.get(ApiKey, key_id)
        if row is None or row.tenant_id != current_tenant():
            raise HTTPException(404, "api key not found")
        row.quota_mode = body.mode
        s.add(AuditLog(tenant_id=row.tenant_id, actor=current_actor()["name"],
                       action="settings.api_key_quota_mode",
                       detail={"name": row.name, "mode": body.mode}))
        await s.commit()
    return {"id": key_id, "quota_mode": body.mode}


class KeyAllocateBody(BaseModel):
    amount: float                           # + carve out of tenant pool, - reclaim


@router.post("/api-keys/{key_id}/allocate")
async def allocate_key_budget(key_id: str, body: KeyAllocateBody):
    """Move budget between the tenant paid pool and a key's allocated budget
    (§12.7: ERP integrations, tests, temporary partners each get their own
    envelope). Both directions are guarded atomic updates; every move is a
    key_transfer ledger row."""
    import json as _json

    from sqlalchemy import update as _update

    from app.billing import engine as billing
    from app.models import ApiKey, CreditAccount, CreditLedger

    _require_admin()
    if body.amount == 0:
        raise HTTPException(400, "amount must be non-zero")
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        key = await s.get(ApiKey, key_id)
        if key is None or key.tenant_id != tenant:
            raise HTTPException(404, "api key not found")
        key_name = key.name              # capture before expire (async ORM rule)
        amt = abs(body.amount)
        acct = await billing.ensure_account(s, tenant)
        if body.amount > 0:
            # pool -> key: the pool must actually hold that much unfrozen paid
            res = await s.execute(
                _update(CreditAccount)
                .where(CreditAccount.tenant_id == tenant,
                       CreditAccount.paid_balance >= amt,
                       CreditAccount.paid_balance + CreditAccount.gift_balance
                       - CreditAccount.frozen >= amt)
                .values(paid_balance=CreditAccount.paid_balance - amt))
            s.expire(acct)
            if res.rowcount == 0:
                raise HTTPException(400, "租户可用余额不足以划拨该金额")
            await s.execute(_update(ApiKey).where(ApiKey.id == key_id)
                            .values(allocated_balance=ApiKey.allocated_balance + amt))
        else:
            # key -> pool: only the key's unfrozen remainder can come back
            res = await s.execute(
                _update(ApiKey)
                .where(ApiKey.id == key_id,
                       ApiKey.allocated_balance - ApiKey.allocated_frozen >= amt)
                .values(allocated_balance=ApiKey.allocated_balance - amt))
            if res.rowcount == 0:
                raise HTTPException(400, "该 Key 可用额度不足以回收该金额")
            await s.execute(_update(CreditAccount)
                            .where(CreditAccount.tenant_id == tenant)
                            .values(paid_balance=CreditAccount.paid_balance + amt))
            s.expire(acct)
        s.expire(key)
        direction = "to_key" if body.amount > 0 else "to_tenant"
        s.add(CreditLedger(
            tenant_id=tenant, kind="key_transfer", bucket="paid", amount=amt,
            note=_json.dumps({"direction": direction, "key_id": key_id,
                              "key_name": key_name}, ensure_ascii=False),
            balance_snapshot=await billing.available(s, tenant)))
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="settings.api_key_allocate",
                       detail={"key_id": key_id, "amount": body.amount}))
        await s.commit()
        key = await s.get(ApiKey, key_id)
        return {"id": key_id, "quota_mode": key.quota_mode,
                "allocated_balance": round(key.allocated_balance, 4),
                "allocated_frozen": round(key.allocated_frozen, 4)}


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
            await mailer.send_templated(
                s, body.to, "SMTP 测试邮件",
                title="发信配置测试",
                greeting="您好：",
                lines=["这是一封来自智能文档识别平台的测试邮件。",
                       "收到本邮件即代表平台发信配置可用，邀请与密码重置邮件将正常送达。"],
                footer_lines=["本邮件由系统自动发送，请勿直接回复。"])
        except mailer.MailerNotConfigured:
            raise HTTPException(409, "SMTP 未配置")
        except Exception as e:
            raise HTTPException(502, f"发送失败：{e}")
    return {"status": "sent", "to": body.to}


# —— tenant-defined model channels (2026-08-26) ——
# providers.yaml stays UI-uneditable config-as-code (§11.10); this is the
# additive layer so an admin can trial a new OpenAI-compatible endpoint (a new
# multimodal model, a self-hosted gateway) without shipping a release.
# Security posture, per §6.2.2: admin-only, key encrypted at rest with the same
# Fernet secret as the SMTP password, never echoed back in any response, and
# every create/delete lands in the audit log with the name only.

class CustomProviderBody(BaseModel):
    base_url: str
    model: str = ""                     # empty = same as the channel name
    api_key: str | None = None          # None/empty = keep the stored key
    vision: bool = False                # channel accepts image input
    extra_body: dict = {}               # vendor options, e.g. {"enable_thinking": false}


def _custom_public(name: str, entry: dict) -> dict:
    return {"name": name, "model": entry.get("model") or "",
            "base_url": entry.get("base_url") or "",
            "vision": bool(entry.get("vision")),
            "has_key": bool(entry.get("api_key_enc")),
            "extra_body": dict(entry.get("extra_body") or {})}


@router.get("/custom-providers")
async def list_custom_providers():
    """Registered channels. Key material never appears here — only has_key."""
    from app.extraction import custom_providers

    _require_admin()
    sf = session_factory()
    async with sf() as s:
        stored = await custom_providers.load_raw(s, current_tenant())
    return {"providers": [_custom_public(n, e) for n, e in sorted(stored.items())
                          if isinstance(e, dict)]}


@router.put("/custom-providers/{name}")
async def put_custom_provider(name: str, body: CustomProviderBody):
    from app.extraction import custom_providers

    _require_admin()
    tenant = current_tenant()
    sf = session_factory()
    try:
        async with sf() as s:
            # a channel with no key is unusable; refuse before writing rather
            # than storing a dead definition and reporting the problem after
            stored = await custom_providers.load_raw(s, tenant)
            had_key = bool((stored.get(name.strip()) or {}).get("api_key_enc"))
            if not had_key and not (body.api_key or "").strip():
                raise HTTPException(400, "首次登记必须填写 API Key")
            out = await custom_providers.put(
                s, tenant, name, body.base_url, body.model, body.api_key,
                vision=body.vision, extra_body=body.extra_body)
            s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                           action="settings.custom_provider_set",
                           detail={"provider": out["name"],
                                   "base_url": out["base_url"],
                                   "model": out["model"]}))
            await s.commit()
    except custom_providers.CustomProviderError as e:
        raise HTTPException(400, str(e)) from e
    return out


@router.delete("/custom-providers/{name}")
async def delete_custom_provider(name: str):
    from app.extraction import custom_providers

    _require_admin()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        removed = await custom_providers.remove(s, tenant, name)
        if not removed:
            raise HTTPException(404, f"unknown channel: {name}")
        s.add(AuditLog(tenant_id=tenant, actor=current_actor()["name"],
                       action="settings.custom_provider_removed",
                       detail={"provider": name}))
        await s.commit()
    return {"provider": name, "removed": True}


@router.post("/custom-providers/{name}/test")
async def test_custom_provider(name: str):
    """One real minimal call against the channel.

    A "saved" credential proves nothing — this is what turns 登记成功 into
    「这个通道真的能出字」. Returns the model's own reply so a wrong base_url or
    a model id the vendor doesn't recognise shows up as an error, not as a
    silent zero-field extraction three screens later.
    """
    import asyncio

    from app.extraction import custom_providers
    from app.extraction.provider_client import ProviderError, chat_json

    _require_admin()
    tenant = current_tenant()
    sf = session_factory()
    async with sf() as s:
        await custom_providers.warm(s, tenant, force=True)
    p = custom_providers.get(tenant, name)
    if p is None:
        raise HTTPException(404, f"unknown channel: {name}")
    if not p["api_key"]:
        raise HTTPException(400, "该通道没有可用的 API Key")
    provider = {"name": name, "model": p["model"], "base_url": p["base_url"],
                "api_key": p["api_key"], "extra_body": dict(p["extra_body"])}
    messages = [{"role": "system", "content": "只输出 JSON，无任何解释。"},
                {"role": "user", "content": '回复 {"ok": true}'}]
    try:
        data, usage = await asyncio.to_thread(chat_json, messages, provider,
                                              timeout=45.0, retries=0)
    except ProviderError as e:
        raise HTTPException(502, f"调用失败：{str(e)[:300]}") from e
    return {"provider": name, "ok": True, "model": p["model"],
            "reply": data, "usage": usage}
