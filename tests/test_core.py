"""M1 walking-skeleton tests: compiler, validators, confidence, and the full
API loop (create skill -> publish -> submit file -> extraction with mocked LLM
-> status with live-verified result schema). LLM and parser are faked — no
tokens burned (feasibility v2.0 §3.2 test discipline).
"""
import asyncio
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.extraction.confidence import score_field
from app.extraction.validators import run_validators
from app.parsers.base import UDR, Block, Page
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import FieldSpec, SkillPackage, Validator

UDR_SAMPLE = UDR(pages=[Page(page_no=1, width=100, height=100, blocks=[
    Block(text="发票号码 INV-2026-001", bbox=[1, 2, 3, 4]),
    Block(text="总金额 1,026.50", bbox=[5, 6, 7, 8]),
])], full_markdown="发票号码 INV-2026-001\n总金额 1,026.50", parser="test")

PKG = SkillPackage(
    skill_code="invoice_test", name="发票测试",
    fields=[
        FieldSpec(name="invoice_no", instruction="发票号码", required=True),
        FieldSpec(name="total", type="number"),
        FieldSpec(name="expense_type", mode="inferred", type="enum",
                  enum_values=["差旅", "办公"]),
    ],
    validators=[Validator(type="required", field="invoice_no"),
                Validator(type="regex", field="invoice_no", pattern=r"INV-\d{4}-\d{3}")],
)


def test_compiler_builds_contract_and_fewshot():
    msgs = compile_messages(PKG, UDR_SAMPLE)
    assert msgs[0]["role"] == "system"
    body = msgs[-1]["content"]
    assert "invoice_no" in body and "INV-2026-001" in body
    assert "推断字段" in body        # inferred mode surfaces in prompt discipline


def test_validators_rule_channel():
    ok = run_validators({"invoice_no": "INV-2026-001"}, PKG.validators)
    assert ok == {}
    bad = run_validators({"invoice_no": ""}, PKG.validators)
    assert "invoice_no" in bad


def test_confidence_scale():
    # exact match in a block -> 3 with bbox backfill
    score, page, bbox = score_field("INV-2026-001", UDR_SAMPLE, False, False, False)
    assert (score, page, bbox) == (3, 1, [1.0, 2.0, 3.0, 4.0])
    # unlocatable verbatim value -> 0 (suspected hallucination)
    score, _, _ = score_field("STAPLES", UDR_SAMPLE, False, False, False)
    assert score == 0
    # inferred with reasoning -> 2, without -> 0
    assert score_field("差旅", UDR_SAMPLE, False, True, True)[0] == 2
    assert score_field("差旅", UDR_SAMPLE, False, True, False)[0] == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_api_full_loop(tmp_path, monkeypatch):
    # isolated DB/data per test run
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    # fake parser: route everything to a canned UDR (no external calls)
    from app.parsers import router as prouter
    monkeypatch.setattr(prouter, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_SAMPLE)

    # fake LLM: OpenAI-compatible JSON reply via respx-free monkeypatch
    def fake_extract_llm(messages, provider, timeout=120.0, retries=2, transport=None):
        return ({"invoice_no": "INV-2026-001", "total": "1,026.50",
                 "expense_type": {"value": "差旅", "reasoning": "出租车费"}},
                {"prompt_tokens": 100, "completion_tokens": 50})
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json", fake_extract_llm)
    monkeypatch.setattr(pipe, "resolve_provider",
                        lambda name: {"name": "fake", "model": "fake",
                                      "base_url": "http://fake", "api_key": "x"})

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            # 1. create + publish skill
            r = await client.post("/api/v1/skills", json={"package": PKG.model_dump()})
            assert r.status_code == 201, r.text
            r = await client.post("/api/v1/skills/invoice_test/versions/1/publish")
            assert r.status_code == 200

            # 2. submit a file
            r = await client.post(
                "/api/v1/process",
                files={"files": ("inv.pdf", b"%PDF-fake", "application/pdf")},
                data={"skill_code": "invoice_test"})
            assert r.status_code == 202, r.text
            tid = r.json()["transaction_id"]

            # 3. wait for the in-process runner
            for _ in range(50):
                await asyncio.sleep(0.1)
                r = await client.get(f"/api/v1/status/{tid}")
                if r.json()["status"] in ("completed", "pending_verification", "error"):
                    break
            body = r.json()
            f = body["files"][0]
            assert f["status"] in ("completed", "pending_verification"), f
            res = f["result"]
            # live-verified result schema: {$value,$confidence,$bbox,$pages}
            assert res["invoice_no"]["$value"] == "INV-2026-001"
            assert res["invoice_no"]["$confidence"] == 3
            assert res["invoice_no"]["$bbox"] == [1.0, 2.0, 3.0, 4.0]
            assert res["expense_type"]["inferred"] is True
            assert res["expense_type"]["$reasoning"]

            # 4. cross-tenant probe must 404 (design §11.2 CI case)
            r = await client.get(f"/api/v1/status/{tid}",
                                 headers={"X-Tenant-Id": "other"})
            assert r.status_code == 404

            # 5. shadow billing wrote a meter entry (§12.6)
            from sqlalchemy import select
            from app.db import session_factory
            from app.models import CreditLedger
            async with session_factory()() as s:
                rows = (await s.execute(select(CreditLedger))).scalars().all()
                assert len(rows) == 1 and rows[0].kind == "shadow_meter"
