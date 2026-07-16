"""Extraction LLM client — one OpenAI-compatible path for all vendors
(design v0.2 §6: DashScope/DeepSeek/OpenAI/vLLM all speak the same protocol).

M2 resilience (the one failure mode the M1 acceptance actually hit — qwen
timeout): skill-level fallback chain + per-provider failure cooldown (a simple
circuit breaker: a provider that just failed is skipped for COOLDOWN seconds so
a flaky vendor doesn't stall every task). LiteLLM remains an M3 swap option;
this covers the proven need without the heavy dependency.
"""
import json
import os
import threading
import time

import httpx

from app.config import load_providers


class ProviderError(RuntimeError):
    pass


class _Cooldown:
    """Thread-safe failure cooldown (runner executes in worker threads)."""

    def __init__(self, seconds: float = 60.0):
        self.seconds = seconds
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def failed(self, name: str) -> None:
        with self._lock:
            self._until[name] = time.time() + self.seconds

    def available(self, name: str) -> bool:
        with self._lock:
            return time.time() >= self._until.get(name, 0.0)


cooldown = _Cooldown()


def resolve_provider(name: str | None) -> dict:
    cfg = load_providers()
    pname = name or cfg["active"]
    p = cfg["providers"].get(pname)
    if p is None:
        raise ProviderError(f"provider not configured: {pname}")
    # tenant BYOK first (§11.10; cache warmed by the async caller), platform env second
    from app.extraction import byok
    from app.tenancy import current_tenant
    key = byok.get(current_tenant(), p.name) \
        or (os.environ.get(p.api_key_env, "").strip() if p.api_key_env else "")
    return {"name": p.name, "model": p.model, "base_url": p.base_url, "api_key": key}


def chat_json_with_fallback(messages: list[dict], provider_names: list[str | None],
                            transport: httpx.BaseTransport | None = None
                            ) -> tuple[dict, dict, str]:
    """Try providers in order (skill binding: extractor -> fallback), skipping
    any in failure cooldown. Returns (json, usage, provider_used). A provider
    in cooldown is only used if it is the last remaining option."""
    chain = [resolve_provider(n) for n in provider_names] or [resolve_provider(None)]
    errors: list[str] = []
    candidates = [p for p in chain if cooldown.available(p["name"])] or chain[-1:]
    for p in candidates:
        try:
            data, usage = chat_json(messages, p, transport=transport)
            return data, usage, p["name"]
        except ProviderError as e:
            cooldown.failed(p["name"])
            errors.append(str(e))
    raise ProviderError(" | ".join(errors) or "no provider available")


def chat_json(messages: list[dict], provider: dict, timeout: float = 120.0,
              retries: int = 2, transport: httpx.BaseTransport | None = None) -> tuple[dict, dict]:
    """Call chat/completions expecting a JSON object reply.
    Returns (parsed_json, usage). Retries only transient network errors —
    idempotent by nature (pure read of the model)."""
    body = {
        "model": provider["model"],
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, transport=transport) as client:
                resp = client.post(f"{provider['base_url'].rstrip('/')}/chat/completions",
                                   headers=headers, json=body)
                resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return _parse_json(content), usage
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
            last = e                       # transient: backoff and retry
            time.sleep(1.5 * (attempt + 1))
        except httpx.HTTPStatusError as e:
            raise ProviderError(
                f"{provider['name']} HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}") from e
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{provider['name']} malformed response") from e
    raise ProviderError(f"{provider['name']} unreachable after retries: {last}")


def _parse_json(content: str) -> dict:
    """Models occasionally wrap JSON in ```json fences despite instructions."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ProviderError(f"model did not return valid JSON: {text[:200]}") from e
