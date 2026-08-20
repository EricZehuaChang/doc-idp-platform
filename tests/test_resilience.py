"""Fallback chain + cooldown tests (the failure mode M1 acceptance hit live)."""
import json

import httpx
import pytest
import respx

from app.extraction import provider_client as pc


@pytest.fixture(autouse=True)
def fresh_cooldown(monkeypatch):
    monkeypatch.setattr(pc, "cooldown", pc._Cooldown(seconds=60))


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch):
    def fake_resolve(name):
        n = name or "primary"
        return {"name": n, "model": f"m-{n}",
                "base_url": f"https://{n}.example/v1", "api_key": "k"}
    monkeypatch.setattr(pc, "resolve_provider", fake_resolve)


def _ok_response(content='{"a": "1"}'):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@respx.mock
def test_fallback_used_when_primary_times_out():
    respx.post("https://primary.example/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("down"))
    respx.post("https://backup.example/v1/chat/completions").mock(
        return_value=_ok_response())
    data, usage, used = pc.chat_json_with_fallback(
        [{"role": "user", "content": "x"}], ["primary", "backup"])
    assert data == {"a": "1"} and used == "backup"
    # primary entered cooldown: next call skips it entirely (no HTTP attempt)
    assert not pc.cooldown.available("primary")


@respx.mock
def test_cooled_provider_skipped_then_only_option_still_tried():
    respx.post("https://backup.example/v1/chat/completions").mock(
        return_value=_ok_response('{"b": "2"}'))
    pc.cooldown.failed("primary")
    data, _, used = pc.chat_json_with_fallback(
        [{"role": "user", "content": "x"}], ["primary", "backup"])
    assert used == "backup"          # primary skipped without an HTTP attempt

    # when the cooled provider is the ONLY option, it is still tried
    respx.post("https://primary.example/v1/chat/completions").mock(
        return_value=_ok_response('{"c": "3"}'))
    data, _, used = pc.chat_json_with_fallback(
        [{"role": "user", "content": "x"}], ["primary"])
    assert used == "primary"


@respx.mock
def test_all_fail_raises_with_joined_errors():
    respx.post("https://primary.example/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom"))
    respx.post("https://backup.example/v1/chat/completions").mock(
        return_value=httpx.Response(429, text="rate"))
    with pytest.raises(pc.ProviderError) as e:
        pc.chat_json_with_fallback([{"role": "user", "content": "x"}],
                                   ["primary", "backup"])
    assert "500" in str(e.value) and "429" in str(e.value)


@respx.mock
def test_provider_extra_body_is_forwarded():
    route = respx.post("https://primary.example/v1/chat/completions").mock(
        return_value=_ok_response())
    pc.chat_json(
        [{"role": "user", "content": "x"}],
        {"name": "primary", "model": "m-primary",
         "base_url": "https://primary.example/v1", "api_key": "k",
         "extra_body": {"enable_thinking": False}},
        retries=0)
    sent = json.loads(route.calls[0].request.content)
    assert sent["enable_thinking"] is False
