"""kind="layout" skills: the parsed region map (text + boxes + page dims) is
returned next to `result` on /status and /documents. Pure layout skills never
call a model; layout skills with fields run normal extraction on top. Parser
and LLM are faked — no external calls, no tokens.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.extraction.layout import layout_view
from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, SkillPackage
from app.skillengine.validation import validate_package

UDR_2P = UDR(pages=[
    Page(page_no=1, width=595.2756, height=841.8898, blocks=[
        Block(type="title", text="个人简历", bbox=[72.004, 60.0, 144.0, 72.0]),
        Block(text="本科 物联网工程", bbox=[72.0, 147.6, 168.0, 159.6],
              chars=[[72.0, 147.6, 84.0, 159.6], [84.0, 147.6, 96.0, 159.6], None,
                     [108.0, 147.6, 120.0, 159.6], [120.0, 147.6, 132.0, 159.6],
                     [132.0, 147.6, 144.0, 159.6], [144.0, 147.6, 156.0, 159.6],
                     [156.0, 147.6, 168.0, 159.6]]),
    ]),
    Page(page_no=2, width=1240, height=1754, blocks=[
        Block(type="table", text="<table>…</table>", bbox=[100, 200, 1100, 900],
              confidence=0.97),
    ]),
], full_markdown="个人简历\n本科 物联网工程", parser="test")


def test_layout_view_flattens_regions_with_page_dims_and_chars():
    view = layout_view(UDR_2P)
    assert view["parser"] == "test"
    assert view["pages"] == [{"page": 1, "width": 595.28, "height": 841.89},
                             {"page": 2, "width": 1240.0, "height": 1754.0}]
    r = view["regions"]
    assert [(x["page"], x["index"], x["type"]) for x in r] == [
        (1, 1, "title"), (1, 2, "text"), (2, 1, "table")]
    assert r[0]["bbox"] == [72.0, 60.0, 144.0, 72.0]       # rounded to 2 dp
    assert "chars" not in r[0]                              # no glyph geometry
    assert len(r[1]["chars"]) == len(r[1]["text"]) and r[1]["chars"][2] is None
    assert r[2]["confidence"] == 0.97


def test_layout_view_drops_off_page_watermark_glyphs():
    """Recruiting-site resumes carry a text-layer watermark: stray glyphs
    merged into real lines and parked off-page (negative x). Region boxes
    span the on-page glyphs only; all-off-page regions are skipped."""
    udr = UDR(pages=[Page(page_no=1, width=596, height=842, blocks=[
        Block(text="~", bbox=[-14, 418, -3, 430], chars=[[-14, 418, -3, 430]]),
        Block(text="x 广州", bbox=[-35, 433, 60, 448],
              chars=[[-35, 440, -25, 448], None, [24, 433, 36, 445], [36, 433, 48, 445]]),
    ])], parser="pdfplumber")
    regions = layout_view(udr)["regions"]
    assert len(regions) == 1
    assert regions[0]["index"] == 1 and regions[0]["text"] == "x 广州"
    assert regions[0]["bbox"] == [24.0, 433.0, 48.0, 445.0]


def test_layout_view_keeps_geometry_free_blocks():
    udr = UDR(pages=[Page(page_no=1, blocks=[Block(text="Office 段落")])],
              parser="markitdown")
    region = layout_view(udr)["regions"][0]
    assert region["bbox"] is None and region["text"] == "Office 段落"


def test_validation_rejects_fast_and_advanced_layout():
    fast = SkillPackage(skill_code="lay_fast", kind="layout", processing_mode="fast")
    errs, _ = validate_package(fast, stage="publish")
    assert any(e.get("code") == "layout_requires_balanced" for e in errs)
    adv = SkillPackage(skill_code="lay_adv", kind="layout", skill_mode="advanced")
    errs, _ = validate_package(adv, stage="publish")
    assert any(e.get("code") == "layout_requires_standard" for e in errs)
    ok = SkillPackage(skill_code="lay_ok", kind="layout")
    assert validate_package(ok, stage="publish")[0] == []


def test_layout_skills_default_to_glyph_first_route():
    from app.tasks.runner import parser_pin_for
    assert parser_pin_for(SkillPackage(skill_code="a", kind="layout")) == "layout-auto"
    assert parser_pin_for(SkillPackage(skill_code="b", kind="layout",
                                       parser="opendataloader")) == "opendataloader"
    assert parser_pin_for(SkillPackage(skill_code="c")) is None


def test_layout_auto_route(monkeypatch):
    """Electronic PDF -> pdfplumber; text-less PDF -> OCR chain directly;
    other formats -> the unchanged auto route."""
    import app.parsers.router as router
    from app.parsers.base import ParserUnavailable
    calls = []

    class _Plumber:
        def parse(self, path):
            calls.append(("pdfplumber", path))
            if "scan" in path:
                raise ParserUnavailable("no text layer")
            return UDR(parser="pdfplumber")

    monkeypatch.setattr(router, "_make", lambda name: _Plumber())
    monkeypatch.setattr(router, "_scan_parse", lambda path: UDR(parser="glm-ocr-cloud"))
    monkeypatch.setattr(router, "parse_document", lambda path, pinned=None: UDR(parser="auto"))
    p = router.LayoutAutoParser()
    assert p.parse("a.pdf").parser == "pdfplumber"
    assert p.parse("scan.pdf").parser == "glm-ocr-cloud"
    assert p.parse("a.png").parser == "auto"
    assert calls == [("pdfplumber", "a.pdf"), ("pdfplumber", "scan.pdf")]


def test_layout_rate_has_no_byok_discount():
    from app.billing.engine import rate_for
    assert rate_for({}, "layout", byok=False) == 1.0
    assert rate_for({}, "layout", byok=True) == 1.0


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _run(client, code: str) -> dict:
    r = await client.post(
        "/api/v1/process",
        files={"files": ("resume.pdf", b"%PDF-fake", "application/pdf")},
        data={"skill_code": code})
    assert r.status_code == 202, r.text
    tid = r.json()["transaction_id"]
    for _ in range(50):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/v1/status/{tid}")
        if r.json()["status"] in ("completed", "pending_verification", "error"):
            break
    return r.json()


async def test_layout_skill_api_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_2P)
    # a 2-page file must not reach the multi-doc page classifier (an LLM call)
    import app.extraction.splitter as splitter

    def no_split(udr):
        raise AssertionError("layout skills never split")
    monkeypatch.setattr(splitter, "classify_pages", no_split)

    llm_calls = []

    def fake_llm(messages, chain, transport=None):
        llm_calls.append(1)
        return ({"major": "物联网工程"}, {"prompt_tokens": 10, "completion_tokens": 5}, "fake")
    import app.extraction.pipeline as pipe
    monkeypatch.setattr(pipe, "chat_json_with_fallback", fake_llm)

    pure = SkillPackage(skill_code="layout_only", name="版面坐标", kind="layout")
    with_fields = SkillPackage(skill_code="layout_fields", name="版面坐标+抽取", kind="layout",
                               fields=[FieldSpec(name="major", instruction="专业")])
    plain = SkillPackage(skill_code="plain_extract", name="普通抽取",
                         fields=[FieldSpec(name="major", instruction="专业")])

    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            for pkg in (pure, with_fields, plain):
                r = await client.post("/api/v1/skills", json={"package": pkg.model_dump()})
                assert r.status_code == 201, r.text
                r = await client.post(f"/api/v1/skills/{pkg.skill_code}/versions/1/publish")
                assert r.status_code == 200, r.text

            # 1. pure layout: no model call, empty result, region map returned
            body = await _run(client, "layout_only")
            f = body["files"][0]
            assert body["status"] == "completed" and f["status"] == "completed", f
            assert f["result"] == {} and llm_calls == []
            assert f["page_count"] == 2
            assert f["layout"] == layout_view(UDR_2P)
            docs = (await client.get(
                f"/api/v1/transactions/{body['transaction_id']}/documents")).json()
            assert docs["files"][0]["documents"][0]["layout"] == layout_view(UDR_2P)

            # 2. layout + fields: model extraction on top, same coordinate space
            body = await _run(client, "layout_fields")
            f = body["files"][0]
            assert llm_calls == [1]
            assert f["result"]["major"]["$value"] == "物联网工程"
            assert f["result"]["major"]["$bbox"] == [108.0, 147.6, 168.0, 159.6]
            assert f["layout"]["pages"][0]["width"] == 595.28

            # 3. ordinary extraction skills keep their exact payload shape (they
            # may consult the page classifier: answer "one document")
            monkeypatch.setattr(splitter, "classify_pages", lambda udr: ([[1, 2]], {}))
            body = await _run(client, "plain_extract")
            assert "layout" not in body["files"][0]
            docs = (await client.get(
                f"/api/v1/transactions/{body['transaction_id']}/documents")).json()
            assert "layout" not in docs["files"][0]["documents"][0]

            # 4. every completed file metered its pages (shadow mode)
            from sqlalchemy import select
            from app.db import session_factory
            from app.models import CreditLedger
            async with session_factory()() as s:
                rows = (await s.execute(select(CreditLedger))).scalars().all()
                assert sum(1 for x in rows if x.kind == "shadow_meter") == 3
