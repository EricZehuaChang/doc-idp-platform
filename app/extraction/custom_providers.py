"""Tenant-defined model channels (2026-08-26).

`configs/providers.yaml` stays config-as-code and UI-uneditable (design §11.10):
it is the platform's own vetted inventory. This module is the additive layer on
top of it — an admin can register an extra OpenAI-compatible endpoint (name +
base_url + model + key) from the console to trial a new vendor without a
release. Same shape as BYOK (`byok.py`), and for the same reason: the extraction
hot path runs in worker threads and cannot await, so definitions live
Fernet-encrypted in tenant_settings and are read from a process-local cache that
the async callers warm.

Discipline:
- names may never shadow a yaml provider — a collision is rejected at write
  time, so "which channel did this run use" always has one answer;
- the key is stored encrypted and never leaves the server, in any response.
"""
from sqlalchemy import select

from app.auth import security
from app.config import load_providers
from app.models import TenantSetting

SETTING_KEY = "custom_providers"

# tenant_id -> {name: {model, base_url, api_key, vision, extra_body}}; process-local
_cache: dict[str, dict[str, dict]] = {}
_warmed: set[str] = set()


class CustomProviderError(ValueError):
    """Rejected definition (bad url, reserved name). API turns it into a 400."""


def _decode(entry: dict) -> dict | None:
    enc = entry.get("api_key_enc")
    try:
        key = security.decrypt_value(enc) if enc else ""
    except Exception:                      # secret rotated: definition is dead
        return None
    return {"model": entry.get("model") or "",
            "base_url": entry.get("base_url") or "",
            "api_key": key,
            "no_key": bool(entry.get("no_key")),
            "vision": bool(entry.get("vision")),
            "extra_body": dict(entry.get("extra_body") or {})}


def get(tenant: str, name: str) -> dict | None:
    """Sync read for the extraction hot path. Unwarmed tenant = yaml only."""
    return _cache.get(tenant, {}).get(name)


def names(tenant: str) -> list[str]:
    return sorted(_cache.get(tenant, {}))


async def warm(session, tenant: str, force: bool = False) -> None:
    """Load a tenant's custom channels into the cache (idempotent per process)."""
    if tenant in _warmed and not force:
        return
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    resolved: dict[str, dict] = {}
    for name, entry in ((row.value if row else None) or {}).items():
        if isinstance(entry, dict) and (dec := _decode(entry)) is not None:
            resolved[name] = dec
    _cache[tenant] = resolved
    _warmed.add(tenant)


async def load_raw(session, tenant: str) -> dict:
    """Stored definitions as-is (still encrypted) — for list/update round-trips."""
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    return dict((row.value if row else None) or {})


def validate(name: str, base_url: str, model: str) -> tuple[str, str, str]:
    name = (name or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip() or name
    if not name:
        raise CustomProviderError("通道名称不能为空")
    if len(name) > 64 or any(c.isspace() for c in name):
        raise CustomProviderError("通道名称需在 64 字符内且不含空格")
    if name in load_providers()["providers"]:
        raise CustomProviderError(f"名称 {name} 与平台内置通道重名，请换一个")
    if not base_url.startswith(("http://", "https://")):
        raise CustomProviderError("接口地址需以 http:// 或 https:// 开头")
    return name, base_url, model


async def put(session, tenant: str, name: str, base_url: str, model: str,
              api_key: str | None, vision: bool = False,
              extra_body: dict | None = None, no_key: bool = False) -> dict:
    """Create or update one channel.

    Three distinct key states, and they must stay distinct:
    - `api_key` non-empty  -> set/replace the key;
    - `api_key` None/empty -> keep whatever is stored, so editing the model id
      does not force the operator to retype the secret;
    - `no_key=True`        -> the endpoint takes no auth at all (self-hosted
      vLLM, an internal gateway). Explicit, not "left the field blank":
      chat_json simply omits the Authorization header. providers.yaml already
      ships one such channel (local-vllm with an empty api_key_env), so this is
      a first-class case rather than a loophole.
    """
    name, base_url, model = validate(name, base_url, model)
    stored = await load_raw(session, tenant)
    prev = stored.get(name) if isinstance(stored.get(name), dict) else {}
    enc = prev.get("api_key_enc", "")
    if no_key:
        enc = ""
    elif api_key is not None and api_key.strip():
        enc = security.encrypt_value(api_key.strip())
    stored[name] = {"model": model, "base_url": base_url, "api_key_enc": enc,
                    "no_key": bool(no_key),
                    "vision": bool(vision), "extra_body": dict(extra_body or {})}
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    if row is None:
        session.add(TenantSetting(tenant_id=tenant, key=SETTING_KEY, value=stored))
    else:
        row.value = stored
    entry = _decode(stored[name])
    if entry is not None:
        _cache.setdefault(tenant, {})[name] = entry
    _warmed.add(tenant)
    return {"name": name, "model": model, "base_url": base_url,
            "vision": bool(vision), "has_key": bool(enc), "no_key": bool(no_key)}


async def remove(session, tenant: str, name: str) -> bool:
    stored = await load_raw(session, tenant)
    if name not in stored:
        return False
    stored.pop(name)
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    if row is not None:
        row.value = stored
    _cache.get(tenant, {}).pop(name, None)
    return True
