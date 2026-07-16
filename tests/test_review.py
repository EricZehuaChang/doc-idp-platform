"""Review-loop test: force needs_review -> queue -> lock (conflict 409) ->
field correction (Correction row + confidence 3) -> confirm -> passed +
transaction completed + audit entries. All external calls mocked.
"""
import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, ReviewPolicy, SkillPackage

UDR_SAMPLE = UDR(pages=[Page(page_no=1, blocks=[Block(text="发票号码 INV-1", bbox=[1, 2, 3, 4])])],
                 full_markdown="发票号码 INV-1", parser="test")

PKG = SkillPackage(
    skill_code="review_test", name="必审技能",
    fields=[FieldSpec(name="invoice_no", instruction="发票号码")],
    review_policy=ReviewPolicy(mode="always"),   # force the human loop
)


async def test_review_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback",
                        lambda *a, **k: ({"invoice_no": "INV-1"},
                                         {"prompt_tokens": 10, "completion_tokens": 5},
                                         "fake"))

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            await client.post("/api/v1/skills/review_test/versions/1/publish")
            r = await client.post("/api/v1/process",
                                  files={"files": ("a.pdf", b"%PDF", "application/pdf")},
                                  data={"skill_code": "review_test"})
            tid = r.json()["transaction_id"]
            for _ in range(50):
                await asyncio.sleep(0.1)
                r = await client.get(f"/api/v1/status/{tid}")
                if r.json()["status"] != "processing" and r.json()["files"][0]["status"] != "queued":
                    break
            assert r.json()["files"][0]["status"] == "pending_verification"
            fid = r.json()["files"][0]["file_id"]

            # queue lists it
            r = await client.get("/api/v1/review/queue")
            assert any(item["file_id"] == fid for item in r.json())

            # lock by alice; bob conflicts with 409; edit without lock -> 423
            r = await client.post(f"/api/v1/review/{fid}/lock", headers={"X-User": "alice"})
            assert r.status_code == 200
            r = await client.post(f"/api/v1/review/{fid}/lock", headers={"X-User": "bob"})
            assert r.status_code == 409
            r = await client.patch(f"/api/v1/review/{fid}/fields",
                                   headers={"X-User": "bob"},
                                   json={"edits": [{"field": "invoice_no", "value": "X"}]})
            assert r.status_code == 423

            # alice corrects the field -> Correction row + human confidence 3
            r = await client.patch(f"/api/v1/review/{fid}/fields",
                                   headers={"X-User": "alice"},
                                   json={"edits": [{"field": "invoice_no", "value": "INV-9"}]})
            assert r.json()["corrected_fields"] == ["invoice_no"]
            r = await client.get(f"/api/v1/review/{fid}")
            cell = r.json()["result"]["invoice_no"]
            assert cell["$value"] == "INV-9" and cell["$confidence"] == 3 and cell["$corrected"]

            # confirm -> passed; transaction rolls up to completed
            r = await client.post(f"/api/v1/review/{fid}/confirm",
                                  headers={"X-User": "alice"}, json={"comment": "ok"})
            assert r.json()["status"] == "passed"
            r = await client.get(f"/api/v1/status/{tid}")
            assert r.json()["status"] == "completed"

            from app.db import session_factory
            from app.models import AuditLog, Correction
            async with session_factory()() as s:
                corrections = (await s.execute(select(Correction))).scalars().all()
                assert len(corrections) == 1
                c = corrections[0]
                assert (c.field, c.old_value, c.new_value, c.reviewer) == \
                       ("invoice_no", "INV-1", "INV-9", "alice")
                assert c.skill_code == "review_test" and c.skill_version == 1
                audits = (await s.execute(select(AuditLog))).scalars().all()
                assert any(a.action == "review.passed" for a in audits)
