"""OpenDataLoader parser: bbox y-flip math, CJK gap stripping, the
unavailable->pdfplumber->scan fallback chain, real JVM parsing (auto-skipped
without Java), and the table-escalation rule wired through the runner."""
import shutil

import pytest

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.parsers.opendataloader import (
    OpenDataLoaderParser, flip_bbox, strip_cjk_gaps)


def _text_pdf(path, page_texts: list[str]) -> None:
    """Handcrafted minimal text-layer PDF (Helvetica/ASCII, exact xref
    offsets) — avoids a reportlab/fpdf test dependency; both pdfplumber and
    the opendataloader JVM accept it as a valid document."""
    objs: list[bytes] = []
    n = len(page_texts)
    font_num = 2 + 2 * n + 1
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, text in enumerate(page_texts):
        content_num = 4 + 2 * i
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     f"/Contents {content_num} 0 R "
                     f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>").encode())
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(b"<< /Length " + str(len(stream)).encode()
                    + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

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


def _odl_ready() -> bool:
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError:
        return False
    return shutil.which("java") is not None


needs_java = pytest.mark.skipif(
    not _odl_ready(), reason="needs opendataloader-pdf wheel + java runtime")


# ---------------- pure functions ----------------


def test_flip_bbox_bottom_left_to_top_left():
    """ODL [left, bottom, right, top] (origin bottom-left) -> UDR
    [x0, top, x1, bottom] (origin top-left): y' = H - y, order swaps."""
    assert flip_bbox([72.0, 700.0, 540.0, 730.0], 792.0) == [72.0, 62.0, 540.0, 92.0]
    x0, top, x1, bottom = flip_bbox([10, 20, 110, 50], 200.0)
    assert (x0, x1) == (10.0, 110.0)
    assert top == 150.0 and bottom == 180.0 and top < bottom


def test_strip_cjk_gaps():
    """Line-join spaces between CJK chars are artifacts (Huaqin sample:
    "监 管平台"); Latin word spacing and CJK-Latin spacing are content."""
    assert strip_cjk_gaps("海关智慧监 管平台") == "海关智慧监管平台"
    assert strip_cjk_gaps("Cadmium (Cd) mg/kg") == "Cadmium (Cd) mg/kg"
    assert strip_cjk_gaps("凌动RPA 产品与 AI 能力") == "凌动RPA 产品与 AI 能力"


# ---------------- availability & fallback chain ----------------


def test_parser_unavailable_without_java(tmp_path, monkeypatch):
    from app.parsers import opendataloader as odl_mod
    monkeypatch.setattr(odl_mod.shutil, "which", lambda _: None)
    f = tmp_path / "doc.pdf"
    _text_pdf(f, ["hello"])
    with pytest.raises(ParserUnavailable):
        OpenDataLoaderParser().parse(str(f))


def test_router_falls_back_to_pdfplumber_without_java(tmp_path, monkeypatch):
    """The chain's safety floor: no Java -> behavior identical to the
    pre-upgrade electronic path (pdfplumber parses the text layer)."""
    pytest.importorskip("pdfplumber")
    from app.parsers import opendataloader as odl_mod
    monkeypatch.setattr(odl_mod.shutil, "which", lambda _: None)
    from app.parsers import router as r
    f = tmp_path / "doc.pdf"
    _text_pdf(f, ["fallback text layer content"])
    udr = r.parse_document(str(f))
    assert udr.parser == "pdfplumber"
    assert "fallback text layer" in udr.full_text()


def test_router_routes_no_text_pdf_to_scan_chain(tmp_path, monkeypatch):
    """A PDF whose pages carry no text must end in the OCR chain regardless
    of which electronic parser inspected it first."""
    pytest.importorskip("pdfplumber")
    from app.parsers import router as r
    sentinel = UDR(pages=[Page(page_no=1)], full_markdown="scanned", parser="scan")
    monkeypatch.setattr(r, "_scan_parse", lambda _path: sentinel)
    f = tmp_path / "scan.pdf"
    _text_pdf(f, [""])          # valid PDF, empty text layer
    assert r.parse_document(str(f)) is sentinel


# ---------------- real JVM parse (auto-skip without Java) ----------------


@needs_java
def test_parse_real_pdf_pages_blocks_and_bbox(tmp_path):
    f = tmp_path / "two.pdf"
    _text_pdf(f, ["Alpha invoice line about housing subsidy",
                  "Beta second page travel expense line"])
    udr = OpenDataLoaderParser().parse(str(f))
    assert udr.parser == "opendataloader"
    assert len(udr.pages) == 2
    assert udr.pages[0].width == 612.0 and udr.pages[0].height == 792.0
    assert "Alpha invoice line" in udr.full_markdown
    assert "Beta second page" in udr.full_markdown
    blocks = [b for p in udr.pages for b in p.blocks if b.text.strip()]
    assert blocks, "text blocks must be emitted"
    for b in blocks:
        assert b.bbox is not None
        x0, top, x1, bottom = b.bbox
        assert 0 <= x0 < x1 <= 612.0
        assert 0 <= top < bottom <= 792.0      # top-left origin: top < bottom
    # text was drawn at y=720pt from the bottom -> near the top after flip
    assert blocks[0].bbox[1] < 100.0


@needs_java
def test_parse_real_scanned_pdf_raises(tmp_path):
    """No text layer -> ParserUnavailable (router contract for the OCR chain)."""
    f = tmp_path / "empty.pdf"
    _text_pdf(f, [""])
    with pytest.raises(ParserUnavailable):
        OpenDataLoaderParser().parse(str(f))


# ---------------- table-escalation rule ----------------


def _udr(parser: str, with_table: bool) -> UDR:
    blocks = [Block(type="text", text="正文")]
    if with_table:
        blocks.append(Block(type="table", text="单元格"))
    return UDR(pages=[Page(page_no=1, blocks=blocks)], parser=parser)


def test_escalates_when_odl_found_no_tables(monkeypatch):
    from app.parsers import router as r
    sentinel = UDR(pages=[], full_markdown="ocr", parser="glm-ocr-cloud")
    monkeypatch.setattr(r, "_scan_parse", lambda _path: sentinel)
    assert r.escalate_if_tables_missing("f.pdf", _udr("opendataloader", False)) is sentinel


def test_no_escalation_when_tables_present(monkeypatch):
    from app.parsers import router as r
    monkeypatch.setattr(r, "_scan_parse",
                        lambda _path: pytest.fail("scan tier must not be spent"))
    udr = _udr("opendataloader", True)
    assert r.escalate_if_tables_missing("f.pdf", udr) is udr


def test_no_escalation_for_pdfplumber_output(monkeypatch):
    """pdfplumber never emits table blocks; escalating on it would reroute
    every legacy electronic flow to paid OCR — explicitly out of scope."""
    from app.parsers import router as r
    monkeypatch.setattr(r, "_scan_parse",
                        lambda _path: pytest.fail("scan tier must not be spent"))
    udr = _udr("pdfplumber", False)
    assert r.escalate_if_tables_missing("f.pdf", udr) is udr


def test_escalation_failure_keeps_original_udr(monkeypatch):
    from app.parsers import router as r

    def _boom(_path):
        raise ParserUnavailable("no ocr configured")

    monkeypatch.setattr(r, "_scan_parse", _boom)
    udr = _udr("opendataloader", False)
    assert r.escalate_if_tables_missing("f.pdf", udr) is udr


def test_skill_expects_tables_flag():
    from app.skillengine.schema import FieldSpec, SkillPackage
    from app.tasks.runner import skill_expects_tables
    with_table = SkillPackage(skill_code="s1", fields=[
        FieldSpec(name="total", type="number"),
        FieldSpec(name="items", type="table",
                  columns=[FieldSpec(name="qty", type="number")])])
    without = SkillPackage(skill_code="s2", fields=[
        FieldSpec(name="total", type="number")])
    assert skill_expects_tables(with_table) is True
    assert skill_expects_tables(without) is False
    # entity-list tables are document-wide sweeps, not layout tables: they must
    # not push an electronic doc to the paid OCR tier (masking-skill economics)
    entity_sweep = SkillPackage(skill_code="s3", fields=[
        FieldSpec(name="打码字段", type="table", entity_list=True,
                  columns=[FieldSpec(name="类型"), FieldSpec(name="内容")])])
    assert skill_expects_tables(entity_sweep) is False
