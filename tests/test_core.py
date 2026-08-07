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

from app.extraction.confidence import locate_all, score_field
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


# ---- masking-grade location (2026-08-06): multi-hit + glyph-tight boxes ----

CHAR_W = 10.0


def _charline(text: str, y0: float = 0.0) -> Block:
    """Block laid out 10px per glyph so tight boxes are computable by eye:
    char i spans [i*10, y0, i*10+10, y0+20]. Spaces get None entries (the
    pdfplumber assembler contract for inserted spaces)."""
    chars = [None if ch == " " else
             [i * CHAR_W, y0, (i + 1) * CHAR_W, y0 + 20]
             for i, ch in enumerate(text)]
    return Block(text=text, bbox=[0, y0, len(text) * CHAR_W, y0 + 20], chars=chars)


def test_locate_all_multi_hit_tight_and_block_fallback():
    udr = UDR(pages=[
        Page(page_no=1, blocks=[_charline("电话 13800138000 内线")]),
        Page(page_no=2, blocks=[Block(text="备份电话 13800138000",
                                      bbox=[7, 8, 9, 10])]),
    ], full_markdown="", parser="test")
    hits, exact = locate_all("13800138000", udr)
    assert exact is True and len(hits) == 2
    # page 1: glyph-tight box over the 11 digits starting at text index 3
    assert hits[0] == {"page": 1, "bbox": [30.0, 0.0, 140.0, 20.0]}
    # page 2: no glyph map -> whole-block bbox fallback
    assert hits[1] == {"page": 2, "bbox": [7.0, 8.0, 9.0, 10.0]}


def test_locate_all_normalized_tight_bbox():
    line = _charline("总金额 1,026.50")
    udr = UDR(pages=[Page(page_no=1, blocks=[line])],
              full_markdown="", parser="test")
    hits, exact = locate_all("1026.50", udr)   # thousand-separator insensitive
    assert exact is False and len(hits) == 1
    # covers the printed "1,026.50" run: text indices 4..11 -> x 40..120
    assert hits[0]["bbox"] == [40.0, 0.0, 120.0, 20.0]


def test_table_rows_carry_cell_locations(monkeypatch):
    import app.extraction.pipeline as pipe
    pkg = SkillPackage(skill_code="mask_probe", fields=[
        FieldSpec(name="打码字段", type="table", entity_list=True, columns=[
            FieldSpec(name="类型", mode="inferred"),   # derivation: never located
            FieldSpec(name="内容"),
        ])])
    udr = UDR(pages=[Page(page_no=1, blocks=[_charline("电话 13800138000 内线")])],
              full_markdown="电话 13800138000 内线", parser="test")

    def fake(messages, chain, transport=None):
        return ({"打码字段": [
            {"类型": "PHONE", "内容": "13800138000"},
            {"类型": "NAME", "内容": "查无此人"},      # hallucinated row
            {"类型": "EMPTY", "内容": ""},             # empty cell: no metadata
        ]}, {"prompt_tokens": 1, "completion_tokens": 1}, "fake")
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake)

    result, _usage, needs_review = pipe.extract(udr, pkg)
    rows = result["打码字段"]
    # plain column values untouched (review grid / rows PATCH contract)
    assert rows[0]["类型"] == "PHONE" and rows[0]["内容"] == "13800138000"
    meta = rows[0]["$cells"]
    assert "类型" not in meta                          # inferred column skipped
    assert meta["内容"]["$confidence"] == 3
    assert meta["内容"]["$hits"] == [{"page": 1, "bbox": [30.0, 0.0, 140.0, 20.0]}]
    # hallucinated value: 0-confidence with zero hits — reviewer sees it flagged
    assert rows[1]["$cells"]["内容"] == {"$confidence": 0, "$hits": []}
    assert "$cells" not in rows[2]                     # nothing locatable
    # cell scores never trip the scalar review gate (masking uses mode=always)
    assert needs_review is False


def test_table_cell_hits_carry_percent_coords_when_page_has_dims(monkeypatch):
    """Masking consumers (mask-guard route-A wiring) need page-percent boxes;
    raw parser-space bbox alone is unusable downstream because /status carries
    no page dims. When the UDR page has dims, every hit gains x/y/w/h percent
    (top-left origin); zero-dim pages (markitdown) keep the bare contract."""
    import app.extraction.pipeline as pipe
    pkg = SkillPackage(skill_code="mask_probe", fields=[
        FieldSpec(name="打码字段", type="table", entity_list=True,
                  columns=[FieldSpec(name="内容")])])
    udr = UDR(pages=[Page(page_no=1, width=200.0, height=400.0,
                          blocks=[_charline("电话 13800138000 内线")])],
              full_markdown="电话 13800138000 内线", parser="test")

    def fake(messages, chain, transport=None):
        return ({"打码字段": [{"内容": "13800138000"}]},
                {"prompt_tokens": 1, "completion_tokens": 1}, "fake")
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake)

    result, _usage, _ = pipe.extract(udr, pkg)
    hit = result["打码字段"][0]["$cells"]["内容"]["$hits"][0]
    assert hit["bbox"] == [30.0, 0.0, 140.0, 20.0]      # raw space untouched
    assert hit["x"] == 15.0 and hit["y"] == 0.0          # 30/200, 0/400
    assert hit["w"] == 55.0 and hit["h"] == 5.0          # 110/200, 20/400


def test_compiler_entity_list_sweep_instruction():
    pkg = SkillPackage(skill_code="m", fields=[
        FieldSpec(name="打码字段", type="table", entity_list=True,
                  columns=[FieldSpec(name="内容")])])
    body = compile_messages(pkg, UDR_SAMPLE)[-1]["content"]
    assert "实体清单表" in body and "禁止遗漏" in body


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

    # fake LLM: OpenAI-compatible JSON reply via monkeypatch
    def fake_extract_llm(messages, chain, transport=None):
        return ({"invoice_no": "INV-2026-001", "total": "1,026.50",
                 "expense_type": {"value": "差旅", "reasoning": "出租车费"}},
                {"prompt_tokens": 100, "completion_tokens": 50}, "fake")
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_extract_llm)

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
