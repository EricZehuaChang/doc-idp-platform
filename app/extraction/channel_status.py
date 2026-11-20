"""Last connection-test result per model channel (2026-11-20).

The settings page used to print a test result as loose text under the table,
gone on the next reload, and the platform channels (providers.yaml) had no
test at all. An operator could not tell at a glance which channels actually
answer. This module keeps the outcome of the most recent "测试连接" per
channel so the console can draw a status dot: green = last test passed,
red = last test failed, grey = never tested, or tested under a different
configuration.

Storage: one tenant_settings row (key "channel_status"); a tenant's BYOK keys
and custom channels are tenant-scoped, so their test results are too.

"Different configuration" is detected with a fingerprint of what the call
depends on (model, base URL, key reference, extra body ...). The fingerprint
is computed from config and from stored, already-encrypted key material,
never from a plaintext key, so nothing secret is derived into this row. Write
paths that change a key (BYOK set / remove) additionally call clear(), since a
replaced BYOK key is not visible in the platform-side fingerprint.

The error text kept here is the same short message the test endpoint already
returns to the admin; it never contains the key (provider errors are built
from status code + response body, and the test call sends the key only in the
Authorization header).
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import TenantSetting

SETTING_KEY = "channel_status"
_ERROR_MAX = 300


def fingerprint(parts: dict) -> str:
    """Stable short hash of a channel's call-relevant configuration."""
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def builtin_fingerprint(p, env_key_set: bool, byok_set: bool) -> str:
    """providers.yaml channel: config-as-code fields + which key source is live."""
    return fingerprint({"model": p.model, "base_url": p.base_url,
                        "api_key_env": p.api_key_env or "",
                        "extra_body": dict(p.extra_body or {}),
                        "env_key": env_key_set, "byok": byok_set})


def custom_fingerprint(entry: dict) -> str:
    """Console-registered channel: the stored entry as-is (key still encrypted;
    re-registering with a new key re-encrypts it, which changes the hash)."""
    return fingerprint({k: entry.get(k) for k in
                        ("model", "base_url", "api_key_enc", "no_key", "extra_body",
                         "ocr", "ocr_prompt")})


async def _row(session, tenant: str):
    return (await session.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant,
                                    TenantSetting.key == SETTING_KEY))).scalar_one_or_none()


async def load(session, tenant: str) -> dict:
    row = await _row(session, tenant)
    return dict((row.value if row else None) or {})


async def _save(session, tenant: str, value: dict) -> None:
    row = await _row(session, tenant)
    if row is None:
        session.add(TenantSetting(tenant_id=tenant, key=SETTING_KEY, value=value))
    else:
        row.value = value          # new dict object -> JSON column is marked dirty


async def record(session, tenant: str, name: str, fp: str, ok: bool,
                 latency_ms: int, error: str = "", model: str = "") -> dict:
    """Store one test outcome; the caller commits."""
    stored = await load(session, tenant)
    entry = {"ok": bool(ok), "fp": fp, "latency_ms": int(latency_ms),
             "error": (error or "")[:_ERROR_MAX], "model": model,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    stored[name] = entry
    await _save(session, tenant, stored)
    return entry


async def clear(session, tenant: str, name: str) -> None:
    """Forget a channel's result (its key changed, or the channel was deleted)."""
    stored = await load(session, tenant)
    if stored.pop(name, None) is not None:
        await _save(session, tenant, stored)


def view(entry: dict | None, fp: str) -> dict:
    """Public status of one channel for the console.

    state: ok | fail | untested | stale (tested, but the configuration changed
    since — the old result no longer says anything about the channel)."""
    if not entry:
        return {"state": "untested"}
    out = {"at": entry.get("at"), "latency_ms": entry.get("latency_ms"),
           "model": entry.get("model") or ""}
    if entry.get("fp") != fp:
        return {"state": "stale", **out}
    if entry.get("ok"):
        return {"state": "ok", **out}
    return {"state": "fail", "error": entry.get("error") or "", **out}
