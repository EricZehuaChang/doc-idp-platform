"""POST /api/v1/locate — dictionary term-location channel (KMBP masking demo).
Covers: locate_terms tight-flag semantics, real pdfplumber parse of a
hand-built CJK PDF (multi-page multi-hit, percent coordinates, glyph-tight
boxes), miss reporting, terms validation 422s, scanned-PDF 422 (never a
silent OCR upgrade), auth-on 401, and the pure-compute guarantee (no
Transaction/FileRecord/ledger rows).
"""
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.extraction.confidence import locate_terms
from app.parsers.base import UDR, Block, Page

# ---------------- locate_terms unit level (tight flag drives confidence) ----


def _charline(text: str, y0: float = 0.0) -> Block:
    """Block laid out 10px per glyph (same convention as test_core): char i
    spans [i*10, y0, i*10+10, y0+20]; spaces get None entries."""
    chars = [None if ch == " " else [i * 10.0, y0, (i + 1) * 10.0, y0 + 20]
             for i, ch in enumerate(text)]
    return Block(text=text, bbox=[0, y0, len(text) * 10.0, y0 + 20], chars=chars)


def test_locate_terms_tight_vs_block_fallback():
    udr = UDR(pages=[
        Page(page_no=1, blocks=[_charline("电话 13800138000 内线")]),
        Page(page_no=2, blocks=[Block(text="备份电话 13800138000",
                                      bbox=[7, 8, 9, 10])]),   # no glyph map
    ], parser="test")
    hits = locate_terms("13800138000", udr)
    assert len(hits) == 2
    assert hits[0] == {"page": 1, "bbox": [30.0, 0.0, 140.0, 20.0], "tight": True}
    assert hits[1] == {"page": 2, "bbox": [7.0, 8.0, 9.0, 10.0], "tight": False}


def test_locate_terms_normalized_pass_keeps_tight_box():
    """Separator-insensitive fallback still maps back to source glyphs."""
    udr = UDR(pages=[Page(page_no=1, blocks=[_charline("总金额 1,026.50")])],
              parser="test")
    hits = locate_terms("1026.50", udr)
    assert hits == [{"page": 1, "bbox": [40.0, 0.0, 120.0, 20.0], "tight": True}]
    assert locate_terms("查无此词", udr) == []


# ---------------- hand-built CJK PDF (fitz/PyMuPDF is BANNED: AGPL) ---------


def _cjk_pdf(path: Path, page_texts: list[str]) -> None:
    """Minimal Type0 (STSong-Light / UniGB-UCS2-H) PDF with a real CJK text
    layer — extends the ASCII builder in test_opendataloader_parser to hex
    UTF-16BE strings; pdfminer ships the Adobe-GB1 CMaps so pdfplumber
    extracts per-glyph boxes without any font embedding or new test dep.
    Each glyph advances DW=1000 -> exactly 12pt wide at size 12; lines within
    a page are drawn 20pt apart starting at y=720 (bottom-left origin)."""
    objs: list[bytes] = []
    n = len(page_texts)
    font_num = 2 + 2 * n + 1
    desc_num, fdesc_num = font_num + 1, font_num + 2
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, text in enumerate(page_texts):
        content_num = 4 + 2 * i
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     f"/Contents {content_num} 0 R "
                     f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>").encode())
        parts = ["BT /F1 12 Tf"]
        for j, line in enumerate(text.split("\n")):
            if line:
                hexs = line.encode("utf-16-be").hex().upper()
                parts.append(f"1 0 0 1 72 {720 - 20 * j} Tm <{hexs}> Tj")
        parts.append("ET")
        stream = " ".join(parts).encode()
        objs.append(b"<< /Length " + str(len(stream)).encode()
                    + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append((f"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                 f"/Encoding /UniGB-UCS2-H "
                 f"/DescendantFonts [{desc_num} 0 R] >>").encode())
    objs.append((f"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                 f"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> "
                 f"/FontDescriptor {fdesc_num} 0 R /DW 1000 >>").encode())
    objs.append(b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 "
                b"/FontBBox [0 -199 1000 801] /ItalicAngle 0 /Ascent 800 "
                b"/Descent -199 /CapHeight 800 /StemV 80 >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    path.write_bytes(bytes(out))


def _sample_pdf_bytes(tmp_path: Path) -> bytes:
    """Two pages, 国家安全部 x3 (twice in one line on page 2), 保密局 x2."""
    f = tmp_path / "sample.pdf"
    _cjk_pdf(f, ["国家安全部批复文件 保密局备案",
                 "抄送:国家安全部办公厅、国家安全部机关服务局\n落款:保密局"])
    return f.read_bytes()


# ---------------- API level (auth off: conftest default) --------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Isolated app + DB per test (same insulation as test_core full loop)."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
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
            yield c


async def test_locate_hits_misses_and_percent_boxes(client, tmp_path):
    pytest.importorskip("pdfplumber")
    pdf = _sample_pdf_bytes(tmp_path)
    r = await client.post(
        "/api/v1/locate",
        files={"file": ("sample.pdf", pdf, "application/pdf")},
        data={"terms": json.dumps(["国家安全部", "保密局", "不存在的机构"])})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count"] == 2 and body["parser"] == "pdfplumber"
    assert body["misses"] == ["不存在的机构"]
    hits = body["hits"]
    assert [h["term"] for h in hits].count("国家安全部") == 3
    assert [h["term"] for h in hits].count("保密局") == 2
    for h in hits:
        # percent space, top-left origin: whole box inside the page
        assert 0 <= h["x"] and h["x"] + h["w"] <= 100
        assert 0 <= h["y"] and h["y"] + h["h"] <= 100
        assert h["confidence"] == 3          # pdfplumber glyphs -> tight boxes
        assert h["h"] < 3.0                  # tight: one 12pt line on 792pt page
    # stable reading order (page, y, x)
    assert [(h["page"], h["y"], h["x"]) for h in hits] == sorted(
        (h["page"], h["y"], h["x"]) for h in hits)
    # deterministic x/width from glyph advances: 保密局备案 term at page 1
    # text index 10 -> x0 = 72 + 10*12 = 192pt of 612 = 31.37%, w = 36pt = 5.88%
    p1_bmj = next(h for h in hits if h["term"] == "保密局" and h["page"] == 1)
    assert p1_bmj["x"] == round((72 + 10 * 12) / 612 * 100, 2)
    assert p1_bmj["w"] == round(3 * 12 / 612 * 100, 2)
    # two same-line hits on page 2 keep left-to-right order
    p2_gab = [h for h in hits if h["term"] == "国家安全部" and h["page"] == 2]
    assert len(p2_gab) == 2 and p2_gab[0]["x"] < p2_gab[1]["x"]
    assert p2_gab[0]["y"] == p2_gab[1]["y"]


async def test_locate_dedupes_terms(client, tmp_path):
    pytest.importorskip("pdfplumber")
    pdf = _sample_pdf_bytes(tmp_path)
    r = await client.post(
        "/api/v1/locate",
        files={"file": ("sample.pdf", pdf, "application/pdf")},
        data={"terms": json.dumps(["保密局", "保密局"])})
    assert r.status_code == 200
    assert len(r.json()["hits"]) == 2        # one per occurrence, not per input


async def test_locate_terms_validation_422(client, tmp_path):
    pdf = b"%PDF-not-even-parsed"            # validation fires before parsing
    for bad in ["not json", "[]", '"just a string"', json.dumps([1, 2]),
                json.dumps(["a" * 101]), json.dumps([""]),
                json.dumps([f"t{i}" for i in range(201)])]:
        r = await client.post(
            "/api/v1/locate",
            files={"file": ("x.pdf", pdf, "application/pdf")},
            data={"terms": bad})
        assert r.status_code == 422, (bad[:30], r.status_code)


async def test_locate_scanned_pdf_422_no_ocr_upgrade(client, tmp_path):
    """No text layer -> explicit 422 naming the OCR limitation; the free-chain
    pin means the paid scan tier is never touched (channel economics rule)."""
    pytest.importorskip("pdfplumber")
    f = tmp_path / "scan.pdf"
    _cjk_pdf(f, [""])                        # valid PDF, empty text layer
    r = await client.post(
        "/api/v1/locate",
        files={"file": ("scan.pdf", f.read_bytes(), "application/pdf")},
        data={"terms": json.dumps(["国家安全部"])})
    assert r.status_code == 422
    assert "需 OCR" in r.json()["detail"]


async def test_locate_unsupported_type_422(client):
    r = await client.post(
        "/api/v1/locate",
        files={"file": ("photo.png", b"\x89PNG fake", "image/png")},
        data={"terms": json.dumps(["国家安全部"])})
    assert r.status_code == 422              # images = OCR territory, refused
    assert "需 OCR" in r.json()["detail"]


async def test_locate_writes_no_rows(client, tmp_path):
    """Pure-compute contract: no Transaction/FileRecord, no billing entries,
    nothing persisted under data_dir/files."""
    pytest.importorskip("pdfplumber")
    pdf = _sample_pdf_bytes(tmp_path)
    r = await client.post(
        "/api/v1/locate",
        files={"file": ("sample.pdf", pdf, "application/pdf")},
        data={"terms": json.dumps(["保密局"])})
    assert r.status_code == 200
    from sqlalchemy import func, select
    from app.db import session_factory
    from app.models import CreditLedger, FileRecord, Transaction
    async with session_factory()() as s:
        for model in (Transaction, FileRecord, CreditLedger):
            n = (await s.execute(select(func.count()).select_from(model))).scalar_one()
            assert n == 0, model.__name__
    assert not (tmp_path / "files").exists()


# ---------------- auth surface (same middleware as /process) ----------------


async def test_locate_requires_credential_when_auth_on(tmp_path, monkeypatch):
    pytest.importorskip("pdfplumber")
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    from app.auth import security
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None                  # process-global; isolate per app
    from app.main import create_app
    app = create_app()
    pdf = _sample_pdf_bytes(tmp_path)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            body = {"files": {"file": ("s.pdf", pdf, "application/pdf")},
                    "data": {"terms": json.dumps(["保密局"])}}
            r = await c.post("/api/v1/locate", **body)
            assert r.status_code == 401      # no ticket
            r = await c.post("/api/v1/locate", **body,
                             headers={"Authorization": "Bearer garbage"})
            assert r.status_code == 401      # bogus ticket
            # JWT channel works end-to-end, same as /process
            r = await c.post("/api/v1/auth/login",
                             json={"email": "admin@example.com",
                                   "password": "admin-pass-123"})
            token = r.json()["access_token"]
            r = await c.post("/api/v1/locate", **body,
                             headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200 and len(r.json()["hits"]) == 2
