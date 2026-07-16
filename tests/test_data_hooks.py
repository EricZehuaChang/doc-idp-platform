"""Webhook + Cabinet + Stats integration: register hook -> run task -> events
fired with HMAC signature; cabinet flat rows; skill stats with correction rate."""
import asyncio
import hashlib
import hmac

import httpx
import respx
from httpx import ASGITransport, AsyncClient

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, ReviewPolicy, SkillPackage

UDR_S = UDR(pages=[Page(page_no=1, blocks=[Block(text="发票号 INV-7", bbox=[1, 1, 2, 2])])],
            full_markdown="发票号 INV-7", parser="t")

PKG = SkillPackage(skill_code="hooked", name="hooked",
                   fields=[FieldSpec(name="invoice_no")],
                   review_policy=ReviewPolicy(mode="always"))


@respx.mock
async def test_hooks_cabinet_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda p, pinned=None: UDR_S)
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback",
                        lambda *a, **k: ({"invoice_no": "INV-7"},
                                         {"prompt_tokens": 5, "completion_tokens": 5}, "f"))

    received: list[httpx.Request] = []
    respx.post("https://client.example/hook").mock(
        side_effect=lambda req: (received.append(req), httpx.Response(200))[1])

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/v1/webhooks", json={
                "url": "https://client.example/hook", "secret": "s3cret",
                "events": []})
            assert r.status_code == 201

            await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            await client.post("/api/v1/skills/hooked/versions/1/publish")
            r = await client.post("/api/v1/process",
                                  files={"files": ("a.pdf", b"%PDF", "application/pdf")},
                                  data={"skill_code": "hooked"})
            tid = r.json()["transaction_id"]
            for _ in range(50):
                await asyncio.sleep(0.1)
                st = (await client.get(f"/api/v1/status/{tid}")).json()
                if st["files"][0]["status"] == "pending_verification":
                    break
            fid = st["files"][0]["file_id"]

            # event 1 fired with valid HMAC. Status commits BEFORE the webhook
            # fires, so polling on status can win the race — wait for delivery.
            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.1)
            assert len(received) == 1
            req = received[0]
            assert req.headers["X-IDP-Event"] == "file.pending_verification"
            sig = hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
            assert req.headers["X-IDP-Signature"] == sig

            # review: correct + confirm -> file.passed event
            await client.post(f"/api/v1/review/{fid}/lock", headers={"X-User": "amy"})
            await client.patch(f"/api/v1/review/{fid}/fields", headers={"X-User": "amy"},
                               json={"edits": [{"field": "invoice_no", "value": "INV-8"}]})
            await client.post(f"/api/v1/review/{fid}/confirm", headers={"X-User": "amy"})
            assert any(r.headers["X-IDP-Event"] == "file.passed" for r in received)

            # cabinet: flat row with corrected value
            r = await client.get("/api/v1/cabinet/hooked")
            rows = r.json()["rows"]
            assert rows and rows[0]["invoice_no"] == "INV-8"
            r = await client.get("/api/v1/cabinet/hooked/export.csv")
            assert "INV-8" in r.text

            # stats: correction rate reflects the human fix
            r = await client.get("/api/v1/stats/skills")
            sk = next(s for s in r.json()["skills"] if s["skill_code"] == "hooked")
            assert sk["corrections"] == 1 and sk["decided"] == 1
            assert sk["top_corrected_fields"][0]["field"] == "invoice_no"
