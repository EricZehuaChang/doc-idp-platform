"""Extraction LLM client — one OpenAI-compatible path for all vendors
(design v0.2 §6: DashScope/DeepSeek/OpenAI/vLLM all speak the same protocol).

M1 keeps a thin httpx client with bounded retry (DashScope jitter was reproduced
live several times — resilience is a requirement, not theory). TODO(M2): swap in
LiteLLM router for fallback/cooldown/cost tracking per feasibility v2.0 §3.2.
"""
import json
import os
import time

import httpx

from app.config import load_providers


class ProviderError(RuntimeError):
    pass


def resolve_provider(name: str | None) -> dict:
    cfg = load_providers()
    pname = name or cfg["active"]
    p = cfg["providers"].get(pname)
    if p is None:
        raise ProviderError(f"provider not configured: {pname}")
    key = os.environ.get(p.api_key_env, "").strip() if p.api_key_env else ""
    return {"name": p.name, "model": p.model, "base_url": p.base_url, "api_key": key}


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
