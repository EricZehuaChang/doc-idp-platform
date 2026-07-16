"""Tenant-level BYOK provider keys (design v0.2 §11.10 / §6.1): each tenant
may bring its own model API key; unset falls back to the platform env key.

Keys live Fernet-encrypted in tenant_settings (key="byok"); a process-local
cache makes resolution synchronous for the extraction hot path (provider_client
runs in worker threads and cannot await). The cache is warmed per tenant in
async context (extract_stage / studio routes) and updated in place by the
settings API on every write — reads never hit the DB.
"""
from sqlalchemy import select

from app.auth import security
from app.models import TenantSetting

SETTING_KEY = "byok"

# (tenant_id, provider_name) -> plaintext key; process-local
_cache: dict[tuple[str, str], str] = {}
_warmed: set[str] = set()


def get(tenant: str, provider: str) -> str | None:
    """Sync read for the extraction hot path. Unwarmed tenant = platform key."""
    return _cache.get((tenant, provider))


async def warm(session, tenant: str, force: bool = False) -> None:
    """Load a tenant's BYOK keys into the cache (idempotent per process)."""
    if tenant in _warmed and not force:
        return
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    for k in [k for k in _cache if k[0] == tenant]:
        del _cache[k]
    if row:
        for provider, entry in (row.value or {}).items():
            enc = entry.get("api_key_enc") if isinstance(entry, dict) else None
            if enc:
                try:
                    _cache[(tenant, provider)] = security.decrypt_value(enc)
                except Exception:      # secret rotated: stale entry, skip
                    pass
    _warmed.add(tenant)


async def put(session, tenant: str, provider: str, api_key: str) -> None:
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    value = dict(row.value) if row else {}
    value[provider] = {"api_key_enc": security.encrypt_value(api_key)}
    if row is None:
        session.add(TenantSetting(tenant_id=tenant, key=SETTING_KEY, value=value))
    else:
        row.value = value
    _cache[(tenant, provider)] = api_key
    _warmed.add(tenant)


async def remove(session, tenant: str, provider: str) -> None:
    row = (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()
    if row and provider in (row.value or {}):
        value = dict(row.value)
        value.pop(provider)
        row.value = value
    _cache.pop((tenant, provider), None)


def has_key(tenant: str, provider: str) -> bool:
    return (tenant, provider) in _cache
