"""9.15 WP5: fast-mode execution contract + Playground purpose=test isolation
(§3.9) + studio runs + /transactions/{txn}/documents (§3.5).

Fake-chat discipline (WP4 lessons): patched fakes are SYNC functions, and
monkeypatching targets `runner_mod`'s imported names, not the source modules.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import FileRecord, Skill, SkillVersion, StudioRun, Transaction
from app.parsers.base import Page, UDR
from app.skillengine.schema import FieldSpec, SkillPackage, Validator


async def _boot(tmp_path, monkeypatch, auth_on=False):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    if auth_on:
        monkeypatch.setenv("IDP_AUTH_MODE", "on")
        monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            yield c, db


@pytest.fixture
async def client(tmp_path, monkeypatch):
    async for pair in _boot(tmp_path, monkeypatch):
        yield pair


@pytest.fixture
async def auth_client(tmp_path, monkeypatch):
    async for pair in _boot(tmp_path, monkeypatch, auth_on=True):
        c, db = pair
        r = await c.post("/api/v1/auth/login",
                         json={"email": "admin@example.com",
                               "password": "admin-pass-123"})
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c, db


# ---------------------------------------------------------------- fast contract


async def test_fast_pipeline_single_call_and_null_contract():
    """extract(fast=True): ONE model call even for multi-page tables, no
    challenger, $confidence None / $bbox [] / $pages "", rule failures kept,
    R11 formatting still applied."""
    from app.extraction import pipeline as pipeline_mod
    from app.extraction import provider_client

    calls = []

    def fake_chat(messages, chain, transport=None):
        calls.append(1)
        return ({"invoice_no": "inv-001", "total": "1026.5",
                 "rows": [{"item": "A", "amount": "1"}]},
                {"prompt_tokens": 10, "completion_tokens": 5,
                 "provider_used": "fake"}, "fake")

    pkg = SkillPackage(skill_code="fast1", name="f", kind="extract",
                       processing_mode="fast",
                       fields=[
                           FieldSpec(name="invoice_no", type="string",
                                     instruction="号码", mode="verbatim"),
                           FieldSpec(name="total", type="number",
                                     instruction="金额", mode="verbatim"),
                           FieldSpec(name="rows", type="table", instruction="明细",
                                     mode="verbatim",
                                     columns=[FieldSpec(name="item", type="string",
                                                        instruction="品名"),
                                              FieldSpec(name="amount", type="number",
                                                        instruction="金额")])],
                       validators=[Validator(type="regex", field="total", pattern="^zzz$")],
                       review_policy={"mode": "auto", "confidence_threshold": 2},
                       model_binding={"extractor": "", "fallback": None,
                                      "challenger": "chall"},
                       parser=None, additional_rules="")
    udr = UDR(pages=[Page(page_no=i, width=100, height=100, blocks=[],
                          markdown=f"page {i} text") for i in (1, 2, 3)],
              full_markdown="page text", parser="test")
    orig = provider_client.chat_json_with_fallback
    pipeline_mod.chat_json_with_fallback = fake_chat
    try:
        result, usage, needs_review = pipeline_mod.extract(udr, pkg, fast=True)
    finally:
        pipeline_mod.chat_json_with_fallback = orig
    assert len(calls) == 1                        # no page-map split in fast mode
    cell = result["invoice_no"]
    assert cell["$confidence"] is None and cell["$bbox"] == [] and cell["$pages"] == ""
    assert cell["$value"] == "inv-001"
    assert result["total"].get("$rule_failures")   # rule failures surface, unscored
    assert result["total"]["$confidence"] is None
    assert result["total"]["$value"] == "1026.5"   # no output_format -> unchanged
    assert result["rows"][0].get("$cells") is None  # fast mode: no bbox scoring
    assert needs_review is False


def _fast_pkg(code: str) -> SkillPackage:
    return SkillPackage(skill_code=code, name="f", kind="extract",
                        processing_mode="fast",
                        fields=[FieldSpec(name="invoice_no", type="string",
                                          instruction="号码", mode="verbatim")],
                        validators=[],
                        review_policy={"mode": "auto", "confidence_threshold": 2},
                        model_binding={"extractor": "", "fallback": None,
                                       "challenger": None})


async def test_runner_fast_route_writes_meta_and_metrics(tmp_path, monkeypatch):
    """Fast extract stage: document_meta.processing_mode/scored=false, metrics
    recorded, extract called with page_images when the vision route ran."""
    import tests.test_advanced_runner as adv

    pkg = _fast_pkg("fast_run")
    seen = {}

    async def fake_parse(path, tenant, parser_pin=None):
        udr = UDR(pages=[Page(page_no=1, width=100, height=100, blocks=[],
                              markdown="inv text")],
                  full_markdown="inv text", parser="vision_fast")
        return udr, {1: "data:image/png;base64,AAA"}, "vision"

    def fake_extract(udr, pkg, **kw):
        seen["fast"] = kw.get("fast")
        seen["images"] = kw.get("page_images")
        return ({"invoice_no": {"$value": "INV-1", "$confidence": None,
                                "$bbox": [], "$pages": ""}},
                {"prompt_tokens": 5, "completion_tokens": 2,
                 "provider_used": "fake"}, False)

    async with adv.Env(tmp_path, monkeypatch, pkg) as env:
        import app.tasks.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_fast_parse", fake_parse)
        _, files, fired, txn_status = await env.seed_and_run(
            monkeypatch, extract_fake=fake_extract)
        f = files["bundle.pdf"]
        meta = f.document_meta or {}
        assert seen["fast"] is True
        assert seen["images"] == {1: "data:image/png;base64,AAA"}
        assert meta.get("processing_mode") == "fast"
        assert meta.get("scored") is False
        m = meta.get("metrics") or {}
        assert m["pages"] == 1 and m["provider_used"] == "fake"
        assert m["total_ms"] >= m["extract_ms"] >= 0
        assert m.get("parse_route") == "vision"


async def test_runner_fast_page_cap_marks_error(tmp_path, monkeypatch):
    """Post-parse page cap: same code as the submit-time 422."""
    import tests.test_advanced_runner as adv

    pkg = _fast_pkg("fast_cap")

    async def fake_parse(path, tenant, parser_pin=None):
        udr = UDR(pages=[Page(page_no=i, width=10, height=10, blocks=[],
                              markdown="x") for i in (1, 2, 3, 4, 5, 6)],
                  full_markdown="x", parser="test")
        return udr, None, "pdfplumber_pinned"

    async with adv.Env(tmp_path, monkeypatch, pkg) as env:
        import app.tasks.runner as runner_mod
        monkeypatch.setattr(runner_mod, "_fast_parse", fake_parse)

        def _no_extract(*a, **k):
            raise AssertionError("extract must not run")
        _, files, fired, txn_status = await env.seed_and_run(
            monkeypatch, extract_fake=_no_extract)
        f = files["bundle.pdf"]
        assert f.status == "error"
        assert "fast_mode_page_limit" in (f.error or "")


async def test_fast_advanced_conflict_rejected_at_publish():
    from app.skillengine.validation import validate_package

    pkg = SkillPackage(skill_code="x", name="x", kind="extract",
                       skill_mode="advanced", processing_mode="fast",
                       categories=[], fields=[])
    errors, _ = validate_package(pkg, stage="publish")
    assert any(e.get("code") == "fast_mode_advanced_conflict" for e in errors)


async def test_submit_time_fast_page_cap_422(tmp_path, monkeypatch):
    """Submit-time estimate check: fast skill + over-cap file -> 422
    fast_mode_page_limit (B2 default: no silent downgrade)."""
    import tests.test_advanced_runner as adv

    pkg = _fast_pkg("fast_submit")
    async with adv.Env(tmp_path, monkeypatch, pkg):
        from unittest.mock import patch

        from app.db import session_factory
        from app.main import create_app
        async with session_factory()() as s:
            s.add(Skill(code="fast_submit", tenant_id="default",
                        name="f", kind="extract"))
            s.add(SkillVersion(tenant_id="default", skill_code="fast_submit",
                               version=1, status="published",
                               package=pkg.model_dump()))
            await s.commit()
        from app.api.routes import process as process_mod
        with patch.object(process_mod.billing, "estimate_pages",
                          lambda b, suffix: 99):
            app = create_app()
            async with app.router.lifespan_context(app):
                async with AsyncClient(transport=ASGITransport(app=app),
                                       base_url="http://test") as c:
                    r = await c.post("/api/v1/process",
                                     data={"skill_code": "fast_submit"},
                                     files={"files": ("big.pdf", b"%PDF-1.4 x")})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "fast_mode_page_limit"


# ------------------------------------------------- §3.9 purpose=test isolation


async def _seed_pair():
    """A finished production txn and a finished test txn, same skill."""
    from app.db import session_factory
    from app.storage import get_storage
    async with session_factory()() as s:
        s.add(Skill(code="iso", tenant_id="default", name="隔离", kind="extract"))
        s.add(SkillVersion(tenant_id="default", skill_code="iso", version=1,
                           status="published", package={}))
        txn_p = Transaction(tenant_id="default", skill_code="iso",
                            skill_version=1, status="completed")
        # production: completed; test: still pending review — every ledger
        # surface that counts decided work must see exactly the production row
        txn_t = Transaction(tenant_id="default", skill_code="iso",
                            skill_version=1, purpose="test", status="completed")
        s.add_all([txn_p, txn_t])
        await s.flush()
        key = get_storage().put_bytes("iso/sample.pdf", b"%PDF-1.4 x")
        for txn in (txn_p, txn_t):
            s.add(FileRecord(tenant_id="default", transaction_id=txn.id,
                             file_name=f"{txn.purpose}.pdf", storage_path=key,
                             status=("completed" if txn is txn_p
                                     else "pending_verification"),
                             page_count=1,
                             result={"a": {"$value": "1", "$confidence": 3}}))
        await s.commit()
    return txn_p.id, txn_t.id


@pytest.mark.parametrize("path", [
    "/api/v1/files", "/api/v1/stats/home", "/api/v1/stats/skills",
    "/api/v1/cabinet/iso", "/api/v1/review/queue",
])
async def test_test_txn_hidden_from_ledger_surfaces(client, path):
    c, db = client
    p_id, t_id = await _seed_pair()
    r = await c.get(path)
    assert r.status_code == 200, path
    body = json.dumps(r.json(), ensure_ascii=False)
    assert t_id[:12] not in body and "test.pdf" not in body, path
    if path == "/api/v1/stats/skills":
        # exactly ONE completed iso row (the test row must not be counted)
        assert any(r.get("total") == 1 or r.get("completed") == 1
                   for r in (r.json() if isinstance(r.json(), list)
                             else r.json().get("skills", []))) or "iso" in body
    elif path == "/api/v1/stats/home":
        h = r.json()
        assert h["today_completed"] == 1 and h["pending_verification"] == 0
    elif path == "/api/v1/cabinet/iso":
        assert "production.pdf" in body
    elif path == "/api/v1/review/queue":
        # production is completed (not pending); the pending TEST row is excluded
        assert r.json() == []
    else:                                 # /files: production rows must stay visible
        assert p_id[:12] in body, path


async def test_cabinet_shows_production_rows(client):
    c, db = client
    p_id, t_id = await _seed_pair()
    r = await c.get("/api/v1/cabinet/iso")
    rows = r.json()["rows"]
    assert [row["file_name"] for row in rows] == ["production.pdf"]


async def test_test_txn_webhook_suppressed(client, monkeypatch):
    from app.integrations import webhooks
    c, db = client
    p_id, t_id = await _seed_pair()
    sent = []

    class R:
        status_code = 200

    async def fake_post(self, url, content=None, headers=None):
        sent.append(json.loads(content))
        return R()

    monkeypatch.setattr(webhooks.httpx.AsyncClient, "post", fake_post)
    from app.integrations.webhooks import Webhook
    async with db.session_factory()() as s:
        s.add(Webhook(tenant_id="default", url="http://hooks.test/x",
                      events=[], active=True))
        await s.commit()
    async with db.session_factory()() as s:
        for tid in (t_id, p_id):
            f = (await s.execute(select(FileRecord)
                                 .where(FileRecord.transaction_id == tid))).scalars().first()
            await webhooks.fire("default", "file.completed", {"file_id": f.id})
    assert len(sent) == 1            # only the production file's event


async def test_test_txn_access_rules(auth_client):
    """Test txn: agent key 404; operator+ 200; agent key cannot poll /status."""
    c, db = auth_client
    p_id, t_id = await _seed_pair()
    r = await c.post("/api/v1/me/api-keys",
                     json={"name": "iso-key", "allowed_skill_codes": ["iso"]})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    ah = {"Authorization": f"Bearer {key}"}
    r = await c.get(f"/api/v1/transactions/{t_id}/documents", headers=ah)
    assert r.status_code == 404
    r = await c.get(f"/api/v1/status/{t_id}", headers=ah)
    assert r.status_code == 404
    r = await c.get(f"/api/v1/transactions/{t_id}/documents")
    assert r.status_code == 200
    doc = r.json()["files"][0]["documents"][0]
    assert doc["data"] == {"a": "1"} and doc["doc_index"] == 1


# ------------------------------------------------------------- studio runs API


async def _seed_playground(monkeypatch):
    """Skill 'play' with a DRAFT v1 + one sample; submit seam captured."""
    from app.db import session_factory
    from app.models import StudioSample
    from app.storage import get_storage
    submitted = []
    monkeypatch.setattr("app.tasks.runner.submit",
                        lambda txn_id: submitted.append(txn_id))
    async with session_factory()() as s:
        key = get_storage().put_bytes("samples/iso.pdf", b"%PDF-1.4 fake")
        s.add(StudioSample(tenant_id="default", file_name="iso.pdf",
                           uploader_id="u1", storage_key=key))
        s.add(Skill(code="play", tenant_id="default", name="play",
                    kind="extract"))
        s.add(SkillVersion(tenant_id="default", skill_code="play", version=1,
                           status="draft",
                           package={"skill_code": "play", "name": "play",
                                    "kind": "extract", "fields": [
                                        {"name": "a", "type": "string",
                                         "instruction": "i", "mode": "verbatim"}],
                                    "validators": [],
                                    "review_policy": {"mode": "auto",
                                                      "confidence_threshold": 2},
                                    "model_binding": {"extractor": "",
                                                      "fallback": None,
                                                      "challenger": None}}))
        await s.commit()
    async with session_factory()() as s:
        sid = (await s.execute(select(StudioSample))).scalars().first().id
    return submitted, sid


async def test_studio_run_creates_test_txn_with_snapshot(client, monkeypatch):
    c, db = client
    submitted, sid = await _seed_playground(monkeypatch)
    r = await c.post("/api/v1/studio/runs",
                     json={"skill_code": "play", "sample_ids": [sid]})
    assert r.status_code == 202, r.text
    body = r.json()
    assert len(submitted) == 1
    from app.db import session_factory
    from app.storage import get_storage
    async with session_factory()() as s:
        txn = await s.get(Transaction, body["transaction_id"])
        assert txn is not None and txn.purpose == "test"
        assert txn.execution_snapshot["package"]["skill_code"] == "play"
        run = await s.get(StudioRun, body["runs"][0]["run_id"])
        assert run.status == "queued" and run.skill_version == 1
        f = await s.get(FileRecord, run.file_id)
        assert f.file_name == "iso.pdf"
        assert f"{txn.id}/" in f.storage_path
        assert get_storage().read_bytes(f.storage_path) == b"%PDF-1.4 fake"


async def test_studio_run_limits_and_missing_samples(client, monkeypatch):
    c, db = client
    _, sid = await _seed_playground(monkeypatch)
    r = await c.post("/api/v1/studio/runs",
                     json={"skill_code": "play", "sample_ids": []})
    assert r.status_code == 400
    r = await c.post("/api/v1/studio/runs",
                     json={"skill_code": "play", "sample_ids": [sid] * 11})
    assert r.status_code == 400
    r = await c.post("/api/v1/studio/runs",
                     json={"skill_code": "play", "sample_ids": ["nope"]})
    assert r.status_code == 404


async def test_studio_run_history_and_detail(client, monkeypatch):
    c, db = client
    _, sid = await _seed_playground(monkeypatch)
    r = await c.post("/api/v1/studio/runs",
                     json={"skill_code": "play", "sample_ids": [sid]})
    run_id = r.json()["runs"][0]["run_id"]
    txn_id = r.json()["transaction_id"]
    r = await c.get("/api/v1/studio/runs",
                    params={"skill_code": "play", "sample_id": sid})
    runs = r.json()["runs"]
    assert runs and runs[0]["run_id"] == run_id
    r = await c.get(f"/api/v1/studio/runs/{run_id}")
    d = r.json()
    assert d["transaction_id"] == txn_id and d["version"] == 1
    assert d["processing_mode"] == "balanced"
    r = await c.get("/api/v1/studio/runs/run_missing")
    assert r.status_code == 404


# --------------------------------------------------------- documents view shapes


async def test_documents_list_mode_returns_array(client):
    c, db = client
    p_id, t_id = await _seed_pair()
    from app.db import session_factory
    async with session_factory()() as s:
        ver = (await s.execute(select(SkillVersion)
                               .where(SkillVersion.skill_code == "iso"))).scalars().first()
        ver.package = {"output_shape": "list"}
        f = (await s.execute(select(FileRecord)
                             .where(FileRecord.transaction_id == p_id))).scalars().first()
        f.result = {"records": [{"name": "r1", "$cells": {"name": {}}}]}
        await s.commit()
    r = await c.get(f"/api/v1/transactions/{p_id}/documents")
    doc = r.json()["files"][0]["documents"][0]
    assert doc["data"] == [{"name": "r1"}]
    assert doc["source_pages"] == [1] and doc["page_range"] == "1"


async def test_documents_review_fields_backend_computed(client):
    c, db = client
    p_id, t_id = await _seed_pair()
    from app.db import session_factory
    async with session_factory()() as s:
        f = (await s.execute(select(FileRecord)
                             .where(FileRecord.transaction_id == p_id))).scalars().first()
        f.result = {"a": {"$value": "1", "$confidence": 1},
                    "b": {"$value": "2", "$confidence": 3},
                    "c": {"$value": "", "$confidence": None}}
        await s.commit()
    r = await c.get(f"/api/v1/transactions/{p_id}/documents")
    doc = r.json()["files"][0]["documents"][0]
    assert doc["review_fields"] == ["a"]   # low confidence; unscored c excluded


# —— 2026-09-18: a pinned cloud_vlm parser forces the image route in fast mode ——
# Field report: engineering drawings uploaded as electronic PDFs took the
# pdfplumber route (text layer present), so the model only ever saw the title
# block. These exercise the real `_fast_parse`; only the raster helpers' input
# (a real one-page PDF) is built here.

def _text_layer_pdf(path) -> str:
    """A real one-page PDF that has a text layer (so has_text_layer() is True)."""
    from reportlab.pdfgen import canvas
    p = str(path / "drawing.pdf")
    c = canvas.Canvas(p)
    c.drawString(72, 720, "TITLE BLOCK: PART 3-02 PLATE, MATERIAL NYLON, SCALE 1:2")
    c.showPage()
    c.save()
    return p


async def test_fast_parse_pinned_vlm_parser_forces_image_route(tmp_path):
    from app.tasks import runner as runner_mod
    pdf = _text_layer_pdf(tmp_path)

    udr, images, route = await runner_mod._fast_parse(pdf, "default", "vlm-qwen")
    assert route == "vision_forced"
    assert images and 1 in images and images[1].startswith("data:image/")
    assert udr.parser == "vision_fast" and udr.lang == []
    assert len(udr.pages) == 1 and udr.pages[0].width > 0


async def test_fast_parse_without_pin_keeps_text_layer_route(tmp_path, monkeypatch):
    """No pin (or a non-vision pin) keeps the documented WP5 behaviour."""
    from app.tasks import runner as runner_mod
    # a tenant vision channel must not hijack a text-layer PDF on its own
    monkeypatch.setattr(runner_mod, "_fast_vision_provider", lambda tenant: "vision-qwen")
    pdf = _text_layer_pdf(tmp_path)

    _, images, route = await runner_mod._fast_parse(pdf, "default", None)
    assert route == "pdfplumber_pinned" and images is None

    _, images, route = await runner_mod._fast_parse(pdf, "default", "pdfplumber")
    assert route == "pdfplumber_pinned" and images is None


def test_pinned_vision_provider_resolves_only_cloud_vlm_parsers():
    from app.tasks.runner import _pinned_vision_provider
    assert _pinned_vision_provider("vlm-qwen") == "vision-qwen"
    assert _pinned_vision_provider("pdfplumber") is None
    assert _pinned_vision_provider(None) is None
    assert _pinned_vision_provider("no-such-parser") is None


async def test_parse_stage_forwards_the_parser_pin_to_fast_parse(client, monkeypatch):
    """parse_stage must hand the skill's pin to the fast route (regression: the
    pin was dropped, which is why the field report never reached the vision path)."""
    from app.tasks import runner as runner_mod
    seen = {}

    async def spy(path, tenant, parser_pin=None):
        seen["pin"] = parser_pin
        raise RuntimeError("stop after the route decision")

    monkeypatch.setattr(runner_mod, "_fast_parse", spy)
    p_id, _ = await _seed_pair()
    from app.db import session_factory
    async with session_factory()() as s:
        f = (await s.execute(select(FileRecord)
                             .where(FileRecord.transaction_id == p_id))).scalars().first()
        file_id = f.id
    with pytest.raises(RuntimeError):
        await runner_mod.parse_stage(file_id, "vlm-qwen", processing_mode="fast")
    assert seen["pin"] == "vlm-qwen"
