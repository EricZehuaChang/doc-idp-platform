"""Console-managed OCR service settings (BOC POC 2026-11): the customer's
PaddleOCR endpoint, its access token and auth header, edited by an admin under
Settings -> OCR service instead of in a deploy file.

Platform-level, one row in platform_settings (key "ocr_service"). The token is
write-only: Fernet-encrypted at rest (same helper as the SMTP password), never
returned by any API. The parser hot path runs in worker threads and cannot
reach the DB, so warm() decrypts the row and pushes it into the pure parser
module (app.parsers.paddleocr_http.configure); app startup, every task's
parse stage and every save call warm() — so an edit applies to the next file
without a restart.
"""
import re

from app.auth import security
from app.models import PlatformSetting
from app.parsers import paddleocr_http

SETTING_KEY = "ocr_service"
DEFAULTS = {"enabled": False, "url": "", "token_enc": "", "auth_header": "Authorization",
            "auth_prefix": "Bearer ", "timeout_s": 120, "verify_tls": True, "extra_params": {}}
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_RESERVED_PARAMS = {"file", "fileType"}


class OcrServiceError(ValueError):
    """Rejected settings; the API turns it into a 400."""


async def load(session) -> dict:
    row = await session.get(PlatformSetting, SETTING_KEY)
    return {**DEFAULTS, **(row.value if row and row.value else {})}


def runtime(cfg: dict) -> dict:
    """Stored form -> what the parser needs (plaintext token)."""
    enc = cfg.get("token_enc")
    try:
        token = security.decrypt_value(enc) if enc else ""
    except Exception:              # secret rotated: treat as no token, the test button shows 401
        token = ""
    out = {k: v for k, v in cfg.items() if k != "token_enc"}
    out["token"] = token
    return out


def public_view(cfg: dict) -> dict:
    view = {k: v for k, v in cfg.items() if k != "token_enc"}
    view["has_token"] = bool(cfg.get("token_enc"))
    view["active"] = bool(cfg.get("enabled") and cfg.get("url"))
    return view


def validate(cfg: dict) -> dict:
    url = (cfg.get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise OcrServiceError("接口地址要以 http:// 或 https:// 开头")
    if cfg.get("enabled") and not url:
        raise OcrServiceError("启用前请先填写接口地址")
    header = (cfg.get("auth_header") or "").strip() or "Authorization"
    if not _HEADER_RE.match(header):
        raise OcrServiceError("令牌请求头名称只能包含字母、数字和连字符")
    timeout = int(cfg.get("timeout_s") or 120)
    if not 5 <= timeout <= 600:
        raise OcrServiceError("超时时间要在 5 到 600 秒之间")
    extra = cfg.get("extra_params") or {}
    if not isinstance(extra, dict):
        raise OcrServiceError("附加请求参数必须是 JSON 对象")
    if _RESERVED_PARAMS & set(extra):
        raise OcrServiceError("附加请求参数里不能包含 file 或 fileType")
    return {**cfg, "url": url, "auth_header": header, "timeout_s": timeout,
            "auth_prefix": cfg.get("auth_prefix") if cfg.get("auth_prefix") is not None else "Bearer ",
            "verify_tls": bool(cfg.get("verify_tls", True)), "enabled": bool(cfg.get("enabled")),
            "extra_params": extra}


def merge(old: dict, body: dict) -> dict:
    """Apply an update: token None keeps the stored one, clear_token drops it."""
    cfg = {**old, **{k: v for k, v in body.items() if k not in ("token", "clear_token")}}
    if body.get("clear_token"):
        cfg["token_enc"] = ""
    elif body.get("token"):
        cfg["token_enc"] = security.encrypt_value(body["token"])
    return validate(cfg)


async def save(session, cfg: dict) -> None:
    row = await session.get(PlatformSetting, SETTING_KEY)
    if row is None:
        session.add(PlatformSetting(key=SETTING_KEY, value=cfg))
    else:
        row.value = cfg


async def warm(session) -> None:
    """Push the current settings into the parser module (cheap: one row)."""
    paddleocr_http.configure(runtime(await load(session)))
