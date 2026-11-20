"""Console-registered recognition engines + channel status dots (2026-11-20).

Two console features shipped together:

1. A custom channel can be registered as a recognition engine (`ocr`, with an
   optional `ocr_prompt`). It then appears in the skill editor's parser
   dropdown; a skill that picks it gets its scans and images read by that
   channel — image first, then the prompt, the way PaddleOCR-VL-style models
   are served — while electronic PDFs / Office files keep their free parse.
   Engines never become an automatic vision-extraction channel.
2. Every channel's last connection test is kept and shown as a dot:
   ok / fail / untested / stale (configuration changed since the test).
"""
import io
import json

import httpx
import respx
from httpx import ASGITransport, AsyncClient
from PIL import Image

ENGINE = {"base_url": "https://gw.example/v1", "model": "paddleocrvl16",
          "api_key": "sk-gw", "vision": True, "ocr": True, "ocr_prompt": "OCR:"}


async def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    from app.extraction import custom_providers
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    custom_providers._cache.clear()
    custom_providers._warmed.clear()
    from app.main import create_app
    return create_app()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buf, format="PNG")
    return buf.getvalue()


def _ocr_reply(seen: list, text: str = "OCR TEST 73915"):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    return handler


# ------------------------------------------------------------ recognition engine


async def test_engine_is_listed_as_parser_and_plain_channels_are_not(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.put("/api/v1/settings/custom-providers/paddle", json=ENGINE)
        assert r.status_code == 200, r.text
        assert r.json()["ocr"] is True and r.json()["ocr_prompt"] == "OCR:"
        await c.put("/api/v1/settings/custom-providers/plain",
                    json={"base_url": "https://x.example/v1", "model": "m", "api_key": "k"})
        opts = (await c.get("/api/v1/skills/model-options")).json()
        engines = [p for p in opts["parsers"] if p.get("custom")]
        assert engines == [{"name": "paddle", "type": "custom_ocr", "description": None,
                            "custom": True, "model": "paddleocrvl16"}]
        # built-in parsers are still offered, unchanged
        assert any(p["name"] == "pdfplumber" for p in opts["parsers"])
        # the key never reaches the picker payload
        assert "sk-gw" not in json.dumps(opts)


async def test_engine_name_cannot_shadow_a_builtin_parser(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.put("/api/v1/settings/custom-providers/pdfplumber", json=ENGINE)
        assert r.status_code == 400
        assert "内置解析器" in r.json()["detail"]
        # the same name is fine for a plain channel (no parser namespace involved)
        plain = {k: v for k, v in ENGINE.items() if k not in ("ocr", "ocr_prompt")}
        assert (await c.put("/api/v1/settings/custom-providers/pdfplumber",
                            json=plain)).status_code == 200


async def test_engine_prompt_is_dropped_when_not_an_engine(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.put("/api/v1/settings/custom-providers/v",
                        json={**ENGINE, "ocr": False})
        assert r.json()["ocr"] is False and r.json()["ocr_prompt"] == ""


async def test_scan_goes_to_the_skill_engine_image_first(tmp_path, monkeypatch):
    """End to end through the task runner: a skill that picked the engine gets
    its scanned image read by it, with the engine's prompt after the image."""
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        await c.put("/api/v1/settings/custom-providers/paddle", json=ENGINE)

        from app.db import session_factory
        from app.models import FileRecord, Skill, SkillVersion, Transaction
        from app.storage import get_storage
        async with session_factory()() as s:
            s.add(Skill(code="inv", tenant_id="default", name="发票", kind="extract"))
            s.add(SkillVersion(tenant_id="default", skill_code="inv", version=1,
                               status="published", package={}))
            txn = Transaction(tenant_id="default", skill_code="inv", skill_version=1,
                              status="processing")
            s.add(txn)
            await s.flush()
            key = get_storage().put_bytes("inv/scan.png", _png())
            f = FileRecord(tenant_id="default", transaction_id=txn.id,
                           file_name="scan.png", storage_path=key, status="queued")
            s.add(f)
            await s.commit()
            file_id = f.id

        seen: list[dict] = []
        from app.parsers import router
        from app.tasks import runner
        # pin the real router entry: a test that stubs router.parse_document
        # BEFORE runner is first imported leaves runner bound to its stub
        monkeypatch.setattr(runner, "parse_document", router.parse_document)
        with respx.mock:
            respx.post("https://gw.example/v1/chat/completions").mock(
                side_effect=_ocr_reply(seen, "发票号码 12345678"))
            await runner.parse_stage(file_id, "paddle")

        async with session_factory()() as s:
            f = await s.get(FileRecord, file_id)
            from app.parsers.base import UDR
            udr = UDR.model_validate_json(get_storage().read_bytes(f.udr_path))
    assert udr.parser == "vlm:custom:paddle"
    assert "发票号码 12345678" in udr.full_markdown
    assert len(seen) == 1
    body = seen[0]
    assert body["model"] == "paddleocrvl16"
    content = body["messages"][0]["content"]
    assert [p["type"] for p in content] == ["image_url", "text"]
    assert content[1]["text"] == "OCR:"


async def test_engine_does_not_take_over_electronic_or_office_files(monkeypatch):
    """resolve_pin turns an engine into a scan reader, not a whole-document pin."""
    from app.extraction import custom_providers
    from app.parsers import router
    custom_providers._cache["t1"] = {"paddle": {
        "model": "paddleocrvl16", "base_url": "https://gw.example/v1", "api_key": "k",
        "no_key": False, "vision": True, "extra_body": {}, "ocr": True, "ocr_prompt": "OCR:"}}
    try:
        pin, engine = router.resolve_pin("paddle", "t1")
        assert pin is None and engine is not None
        # built-in parser names stay pins; unknown names are left to fail as before
        assert router.resolve_pin("pdfplumber", "t1") == ("pdfplumber", None)
        assert router.resolve_pin("nope", "t1") == ("nope", None)
        # Office route never calls the engine
        called = []
        monkeypatch.setattr(engine, "parse", lambda path: called.append(path))
        from app.parsers.base import UDR

        class _Office:
            def parse(self, path):
                return UDR(pages=[], full_markdown="docx", parser="markitdown")
        monkeypatch.setattr(router, "_make", lambda name: _Office())
        assert router.parse_document("x.docx", None, engine).parser == "markitdown"
        assert called == []
    finally:
        custom_providers._cache.pop("t1", None)


async def test_engine_failure_falls_back_to_the_default_scan_chain(monkeypatch):
    from app.parsers import router
    from app.parsers.base import UDR, ParserUnavailable

    class _Broken:
        def parse(self, path):
            raise ParserUnavailable("gateway down")

    class _Default:
        def parse(self, path):
            return UDR(pages=[], full_markdown="default", parser="default-ocr")
    monkeypatch.setattr(router, "_make", lambda name: _Default())
    assert router.parse_document("scan.png", None, _Broken()).parser == "default-ocr"


async def test_engines_are_never_picked_as_vision_extraction_channels(monkeypatch):
    from app.extraction import custom_providers, vision_first
    from app.skillengine.schema import FieldSpec, SkillPackage
    from app.tasks import runner
    custom_providers._cache["t2"] = {
        "a-paddle": {"model": "p", "base_url": "u", "api_key": "k", "no_key": False,
                     "vision": True, "extra_body": {}, "ocr": True, "ocr_prompt": "OCR:"},
        "b-vlm": {"model": "v", "base_url": "u", "api_key": "k", "no_key": False,
                  "vision": True, "extra_body": {}, "ocr": False, "ocr_prompt": ""}}
    import app.config as config
    monkeypatch.setenv("IDP_FAST_VISION_PROVIDER", "")
    monkeypatch.setenv("IDP_VISION_FIRST_PROVIDER", "")
    config.get_settings.cache_clear()
    try:
        assert runner._fast_vision_provider("t2") == "b-vlm"
        pkg = SkillPackage(skill_code="s", name="s", fields=[FieldSpec(name="a")],
                           model_binding={"extractor": "a-paddle"})
        assert vision_first.vision_provider(pkg, "t2") == "b-vlm"
    finally:
        custom_providers._cache.pop("t2", None)
        config.get_settings.cache_clear()


# ------------------------------------------------------------------ status dots


async def test_custom_channel_status_ok_fail_stale_and_cleared(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        await c.put("/api/v1/settings/custom-providers/chat",
                    json={"base_url": "https://chat.example/v1", "model": "m1",
                          "api_key": "sk-chat"})

        def status():
            return c.get("/api/v1/settings/custom-providers")

        assert (await status()).json()["providers"][0]["status"] == {"state": "untested"}

        with respx.mock:
            respx.post("https://chat.example/v1/chat/completions").mock(
                return_value=httpx.Response(200, json={
                    "choices": [{"message": {"content": '{"ok": true}'}}]}))
            r = await c.post("/api/v1/settings/custom-providers/chat/test")
        assert r.status_code == 200, r.text
        assert r.json()["status"]["state"] == "ok"
        st = (await status()).json()["providers"][0]["status"]
        assert st["state"] == "ok" and st["model"] == "m1" and st["at"]

        with respx.mock:
            respx.post("https://chat.example/v1/chat/completions").mock(
                return_value=httpx.Response(401, json={"error": "bad key"}))
            r = await c.post("/api/v1/settings/custom-providers/chat/test")
        assert r.status_code == 502
        st = (await status()).json()["providers"][0]["status"]
        assert st["state"] == "fail" and "401" in st["error"]
        assert "sk-chat" not in json.dumps(st)

        # change the model: the old result no longer describes the channel
        await c.put("/api/v1/settings/custom-providers/chat",
                    json={"base_url": "https://chat.example/v1", "model": "m2"})
        assert (await status()).json()["providers"][0]["status"]["state"] == "stale"

        # delete + re-register: no leftover result
        await c.delete("/api/v1/settings/custom-providers/chat")
        await c.put("/api/v1/settings/custom-providers/chat",
                    json={"base_url": "https://chat.example/v1", "model": "m2",
                          "api_key": "sk-chat"})
        assert (await status()).json()["providers"][0]["status"] == {"state": "untested"}


async def test_engine_test_reads_an_image_the_way_a_scan_does(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.router.lifespan_context(app), _client(app) as c:
        await c.put("/api/v1/settings/custom-providers/paddle", json=ENGINE)
        seen: list[dict] = []
        with respx.mock:
            respx.post("https://gw.example/v1/chat/completions").mock(
                side_effect=_ocr_reply(seen))
            r = await c.post("/api/v1/settings/custom-providers/paddle/test")
        assert r.status_code == 200, r.text
        assert r.json()["status"]["state"] == "ok"
        content = seen[0]["messages"][0]["content"]
        assert [p["type"] for p in content] == ["image_url", "text"]
        assert content[1]["text"] == "OCR:"
        assert "response_format" not in seen[0]      # not a JSON chat test

        # an answer without the number is a failure, with the reason kept
        with respx.mock:
            respx.post("https://gw.example/v1/chat/completions").mock(
                side_effect=_ocr_reply([], "something else"))
            r = await c.post("/api/v1/settings/custom-providers/paddle/test")
        assert r.status_code == 502
        st = (await c.get("/api/v1/settings/custom-providers")).json()["providers"][0]["status"]
        assert st["state"] == "fail" and "73915" in st["error"]


async def test_builtin_channel_test_and_byok_invalidation(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    from app.config import load_providers
    name, p = next(iter(load_providers()["providers"].items()))
    if p.api_key_env:
        monkeypatch.setenv(p.api_key_env, "sk-platform")
    url = f"{p.base_url.rstrip('/')}/chat/completions"
    async with app.router.lifespan_context(app), _client(app) as c:
        def row():
            return next(x for x in c_list["providers"] if x["name"] == name)

        c_list = (await c.get("/api/v1/settings/providers")).json()
        assert row()["status"] == {"state": "untested"}

        with respx.mock:
            respx.post(url).mock(return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": '{"ok": true}'}}]}))
            r = await c.post(f"/api/v1/settings/providers/{name}/test")
        assert r.status_code == 200, r.text
        c_list = (await c.get("/api/v1/settings/providers")).json()
        assert row()["status"]["state"] == "ok"

        # bringing an own key changes which credential is used: back to grey
        assert (await c.put(f"/api/v1/settings/providers/{name}/key",
                            json={"api_key": "sk-own"})).status_code == 200
        c_list = (await c.get("/api/v1/settings/providers")).json()
        assert row()["status"]["state"] == "untested"

        assert (await c.post("/api/v1/settings/providers/no-such/test")).status_code == 404


async def test_builtin_test_without_any_key_is_a_recorded_failure(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    from app.config import load_providers
    keyed = [(n, p) for n, p in load_providers()["providers"].items() if p.api_key_env]
    name, p = keyed[0]
    monkeypatch.delenv(p.api_key_env, raising=False)
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.post(f"/api/v1/settings/providers/{name}/test")
        assert r.status_code == 502 and "API Key" in r.json()["detail"]
        rows = (await c.get("/api/v1/settings/providers")).json()["providers"]
        st = next(x for x in rows if x["name"] == name)["status"]
        assert st["state"] == "fail"
