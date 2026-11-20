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
import httpx
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
            "extra_body": dict(entry.get("extra_body") or {}),
            "ocr": bool(entry.get("ocr")),
            "ocr_prompt": entry.get("ocr_prompt") or ""}


def get(tenant: str, name: str) -> dict | None:
    """Sync read for the extraction hot path. Unwarmed tenant = yaml only."""
    return _cache.get(tenant, {}).get(name)


def names(tenant: str) -> list[str]:
    return sorted(_cache.get(tenant, {}))


def ocr_engine(tenant: str, name: str) -> dict | None:
    """The channel behind `name` when it is registered as a recognition engine
    (2026-11-20: a skill may pick it in the parser dropdown), else None."""
    entry = get(tenant, name)
    return entry if entry and entry.get("ocr") else None


def is_ocr_engine(entry: dict | None) -> bool:
    """Recognition engines only transcribe pages. Automatic vision picks (fast
    mode, image-first extraction) must never hand them an extraction prompt —
    PaddleOCR-VL-style models answer fixed task prompts, not field schemas."""
    return bool(entry and entry.get("ocr"))


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


# Vendors document the full endpoint (".../v1/chat/completions"), so that is
# what gets pasted into a field labelled "接口地址". The client appends the path
# itself, and the doubled suffix 404s with nothing pointing at the cause — seen
# for real on 2026-08-26. Accept both forms instead of being right about it.
# "/models" joined the list with model discovery (2026-11-19): it is the other
# endpoint people copy out of vendor docs.
_ENDPOINT_SUFFIXES = ("/chat/completions", "/completions", "/models")


def normalize_base_url(base_url: str) -> str:
    """Trim whitespace, trailing slashes and one pasted endpoint suffix."""
    base_url = (base_url or "").strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if base_url.endswith(suffix):
            return base_url[: -len(suffix)].rstrip("/")
    return base_url


def validate(name: str, base_url: str, model: str) -> tuple[str, str, str]:
    name = (name or "").strip()
    base_url = normalize_base_url(base_url)
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


_OCR_PROMPT_MAX = 2000


async def put(session, tenant: str, name: str, base_url: str, model: str,
              api_key: str | None, vision: bool = False,
              extra_body: dict | None = None, no_key: bool = False,
              ocr: bool = False, ocr_prompt: str = "") -> dict:
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

    `ocr` (2026-11-20) registers the channel as a recognition engine: it then
    shows up in the skill editor's parser dropdown and, when a skill picks it,
    reads that skill's scans and images. `ocr_prompt` is the instruction sent
    with each page image; empty keeps the platform's own transcription brief,
    a model trained on fixed task prompts (PaddleOCR-VL: "OCR:") gets its own.
    """
    name, base_url, model = validate(name, base_url, model)
    ocr_prompt = (ocr_prompt or "").strip() if ocr else ""
    if ocr:
        from app.config import load_parsers
        # the parser dropdown and a skill's `parser` field share one namespace:
        # an engine named like a built-in parser would make a pin ambiguous
        if name in load_parsers()["parsers"]:
            raise CustomProviderError(f"名称 {name} 与平台内置解析器重名，请换一个")
        if len(ocr_prompt) > _OCR_PROMPT_MAX:
            raise CustomProviderError(f"识别指令不能超过 {_OCR_PROMPT_MAX} 字")
    stored = await load_raw(session, tenant)
    prev = stored.get(name) if isinstance(stored.get(name), dict) else {}
    enc = prev.get("api_key_enc", "")
    if no_key:
        enc = ""
    elif api_key is not None and api_key.strip():
        enc = security.encrypt_value(api_key.strip())
    stored[name] = {"model": model, "base_url": base_url, "api_key_enc": enc,
                    "no_key": bool(no_key),
                    "vision": bool(vision), "extra_body": dict(extra_body or {}),
                    "ocr": bool(ocr), "ocr_prompt": ocr_prompt}
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
            "vision": bool(vision), "has_key": bool(enc), "no_key": bool(no_key),
            "ocr": bool(ocr), "ocr_prompt": ocr_prompt}


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


# —— model discovery (2026-11-19) ——
# A customer often hands over nothing but an OpenAI-compatible base URL ending
# in /v1 plus a key. Typing the model id by hand is where registrations go
# wrong (a model the vendor does not serve only fails at test time), so the
# console asks the endpoint itself: GET {base_url}/models, the standard
# OpenAI list call, and offers the ids as a dropdown. Nothing is stored here;
# registration still goes through put() above.
#
# The response is external input (§6.2.2): size-capped, only id strings are
# kept, each id length-capped and printable, and the list itself is capped.
# The key travels only as the Bearer header to the URL the admin typed — it is
# never put in an error message or a log line.
_MODEL_LIST_MAX_BYTES = 8 * 1024 * 1024    # OpenRouter's full catalogue is ~1-2 MB
_MODEL_LIST_MAX_ITEMS = 2000
_MODEL_ID_MAX_LEN = 200


class ModelDiscoveryError(RuntimeError):
    """The endpoint could not produce a model list. API turns it into a 502."""


def parse_model_list(payload) -> list[str]:
    """Extract model ids from a /models response, sorted case-insensitively.

    Accepted shapes, in the order vendors actually return them:
    - OpenAI standard        {"data": [{"id": "..."}]}   (also vLLM, DashScope,
      DeepSeek, Zhipu, OpenRouter, one-api / new-api gateways)
    - Ollama / Gemini native {"models": [{"name": "..."}]}
    - bare list              ["...", ...] or [{"id": "..."}]
    """
    items = None
    if isinstance(payload, dict):
        for key in ("data", "models"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    elif isinstance(payload, list):
        items = payload
    if items is None:
        raise ModelDiscoveryError("返回内容里没有模型列表（缺少 data 字段），请确认是 OpenAI 兼容接口")
    ids: set[str] = set()
    for item in items[:_MODEL_LIST_MAX_ITEMS]:
        if isinstance(item, dict):
            item = item.get("id") or item.get("name") or item.get("model")
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and len(item) <= _MODEL_ID_MAX_LEN and item.isprintable():
            ids.add(item)
    return sorted(ids, key=lambda s: (s.lower(), s))


async def discover_models(base_url: str, api_key: str, timeout: float = 20.0,
                          transport: httpx.AsyncBaseTransport | None = None) -> list[str]:
    """GET {base_url}/models and return the model ids the endpoint serves.

    `base_url` must already be normalized; an empty `api_key` means the
    endpoint takes no auth (the same no_key case as put()).
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
    except httpx.TimeoutException as e:
        raise ModelDiscoveryError(f"连接超时（{int(timeout)} 秒内无响应），请检查地址或网络") from e
    except httpx.HTTPError as e:
        raise ModelDiscoveryError(f"无法连接该地址：{type(e).__name__}") from e

    code = resp.status_code
    if code in (401, 403):
        raise ModelDiscoveryError(f"鉴权失败（HTTP {code}），请检查 API Key")
    if code in (404, 405):
        raise ModelDiscoveryError(
            f"该地址没有模型列表接口（HTTP {code}）。请确认地址写到 /v1 为止；"
            "若厂商不提供列模型接口，可直接手动填写模型 ID")
    if not 200 <= code < 300:
        # vendor error bodies sometimes quote the key they were given
        text = resp.text.replace(api_key, "***") if api_key else resp.text
        raise ModelDiscoveryError(f"HTTP {code}：{text[:200]}")
    if len(resp.content) > _MODEL_LIST_MAX_BYTES:
        raise ModelDiscoveryError("返回内容过大，不像是模型列表")
    try:
        payload = resp.json()
    except ValueError as e:
        # typically a web page: the site root was pasted instead of the API root
        raise ModelDiscoveryError("返回的不是 JSON，请确认填的是 API 地址（通常以 /v1 结尾）而不是网页地址") from e
    return parse_model_list(payload)
