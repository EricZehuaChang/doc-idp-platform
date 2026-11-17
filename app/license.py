"""Service authorization switch for private deployments (BOC POC, 2026-11).

With IDP_LICENSE_REQUIRED=true the platform answers only while a license
signed by the vendor's Ed25519 key is installed, unexpired, and issued AFTER
the most recent suspension. `python -m app.license suspend` locks the service
at once (no restart needed); reopening needs a freshly issued license from
scripts/issue_license.py, which runs on the vendor side with the private key
— the key never ships with the image. Default off: existing deployments
behave byte-identically.

This is a deterrent against casual use, not DRM: whoever has root on the host
can tamper with these files or the clock. The expiry date is the backstop when
nobody is on site to run `suspend`.

Storage keys (app/storage seam; on the local backend these are files under
<IDP_DATA_DIR>/license/):
  license/license.json   {"customer", "expires": "YYYY-MM-DD", "issued_at": ISO-8601 UTC,
                          "signature": base64 Ed25519 over signing_payload(...)}
  license/suspended_at   ISO-8601 UTC timestamp of the last suspension

Usage inside the container:
  python -m app.license status | suspend | install <license.json>
"""
import base64
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.storage import get_storage

# Pairs with D:\Claude Code\idp-license-private.pem (outside every repo). Only
# that key can sign a license this build accepts; rotating it voids all
# licenses issued so far.
_PUBLIC_KEY_B64 = "S0pjPg56FVeTZSDkUZH0e4YBH6fLU6bMv6cIhmoGuck="

# probes stay open so the container healthcheck does not restart-loop a
# deliberately locked service
_OPEN_PATHS = {"/healthz", "/readyz"}
_LICENSE_KEY = "license/license.json"
_SUSPENDED_KEY = "license/suspended_at"
_CACHE_TTL = 5.0
_cache: tuple[float, dict] | None = None

LOCKED_MESSAGE = "服务未授权或已暂停，请联系服务提供方"
_LOCKED_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>服务已暂停</title>
<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f5f7fa;color:#1f2d3d;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
div{background:#fff;padding:40px 56px;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.08);
text-align:center}h1{font-size:22px;margin:0 0 12px}p{color:#5a6b7d;margin:0}</style></head>
<body><div><h1>服务已暂停</h1><p>本系统当前未授权或已暂停使用，请联系服务提供方。</p></div></body></html>"""


def signing_payload(customer: str, expires: str, issued_at: str) -> bytes:
    """Canonical bytes the signature covers — scripts/issue_license.py must
    produce exactly the same encoding."""
    return json.dumps({"customer": customer, "expires": expires, "issued_at": issued_at},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read(key: str) -> str | None:
    storage = get_storage()
    return storage.read_bytes(key).decode("utf-8-sig") if storage.exists(key) else None


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _today() -> date:
    # expiry is a calendar date in the customer's timezone: valid through
    # the whole of the `expires` day
    return datetime.now(ZoneInfo(get_settings().output_tz)).date()


def _read_suspended_at() -> datetime | None:
    raw = _read(_SUSPENDED_KEY)
    if raw is None:
        return None
    try:
        return _parse_ts(raw.strip())
    except ValueError:
        # an unreadable marker must not reopen the service
        return datetime.max.replace(tzinfo=timezone.utc)


def verify(data: dict) -> dict:
    """Validate one license dict (signature, expiry, suspension) without
    touching the cache. Returns {"state", ...details}."""
    try:
        customer, expires = str(data["customer"]), str(data["expires"])
        issued_at, signature = str(data["issued_at"]), str(data["signature"])
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))
        key.verify(base64.b64decode(signature), signing_payload(customer, expires, issued_at))
        expires_date = date.fromisoformat(expires)
        issued = _parse_ts(issued_at)
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return {"state": "invalid"}
    info = {"customer": customer, "expires": expires, "issued_at": issued_at}
    suspended = _read_suspended_at()
    if suspended is not None:
        info["suspended_at"] = suspended.isoformat()
        if issued <= suspended:
            return {"state": "suspended", **info}
    if expires_date < _today():
        return {"state": "expired", **info}
    return {"state": "valid", **info}


def check() -> dict:
    """Current license state from disk: valid | missing | invalid | expired | suspended."""
    raw = _read(_LICENSE_KEY)
    if raw is None:
        suspended = _read_suspended_at()
        return {"state": "suspended" if suspended else "missing"}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"state": "invalid"}
    return verify(data if isinstance(data, dict) else {})


def gate_state() -> dict:
    """check() with a short TTL — the gate runs on every request."""
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > _CACHE_TTL:
        _cache = (now, check())
    return _cache[1]


def reset_cache() -> None:
    global _cache
    _cache = None


def suspend(now: datetime | None = None) -> dict:
    """Lock the service: every license issued before this moment stops working."""
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    get_storage().put_bytes(_SUSPENDED_KEY, stamp.encode("utf-8"))
    reset_cache()
    return check()


def install(raw: str) -> dict:
    """Install a license only if it would make the service valid right now;
    a rejected license leaves the current one untouched."""
    try:
        data = json.loads(raw)
    except ValueError:
        return {"state": "invalid"}
    result = verify(data if isinstance(data, dict) else {})
    if result["state"] == "valid":
        get_storage().put_bytes(
            _LICENSE_KEY, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        reset_cache()
    return result


class LicenseGateMiddleware(BaseHTTPMiddleware):
    """Outermost gate: when the switch is on and the license is not valid,
    API calls get a JSON 503 and pages get a static notice."""

    async def dispatch(self, request, call_next):
        if not get_settings().license_required or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        state = gate_state()
        if state["state"] == "valid":
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": LOCKED_MESSAGE, "license_state": state["state"]},
                                status_code=503)
        return HTMLResponse(_LOCKED_PAGE, status_code=503)


_STATE_LABELS = {"valid": "已授权，服务正常", "missing": "未安装授权，服务已锁定",
                 "invalid": "授权无效，服务已锁定", "expired": "授权已过期，服务已锁定",
                 "suspended": "服务已暂停"}


def _print(result: dict) -> None:
    print(_STATE_LABELS.get(result["state"], result["state"]))
    for key, label in (("customer", "客户"), ("expires", "有效期至"),
                       ("issued_at", "签发时间"), ("suspended_at", "暂停时间")):
        if result.get(key):
            print(f"  {label}: {result[key]}")
    if not get_settings().license_required:
        print("  注意：IDP_LICENSE_REQUIRED 未开启，授权状态不影响访问")


def main(argv: list[str]) -> int:
    if argv[:1] == ["status"]:
        result = check()
    elif argv[:1] == ["suspend"]:
        result = suspend()
    elif len(argv) == 2 and argv[0] == "install":
        result = install(Path(argv[1]).read_text(encoding="utf-8-sig"))
        if result["state"] != "valid":
            print("授权文件未安装：", end="")
    else:
        print("用法: python -m app.license status | suspend | install <license.json>")
        return 2
    _print(result)
    # exit code tells the ops script whether the command reached its goal
    expected = "suspended" if argv[0] == "suspend" else "valid"
    return 0 if result["state"] == expected else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
