"""Parser adapters against mocked services: MonkeyOCR zip contract, RapidOCR
polygon->bbox, GLM-OCR data-URI contract, and the scan degradation chain."""
import io
import zipfile

import httpx
import pytest
import respx

from app.parsers.base import ParserUnavailable
from app.parsers.glm_ocr_cloud import GlmOcrCloudParser
from app.parsers.monkeyocr_http import MonkeyOcrHttpParser
from app.parsers.rapidocr_http import RapidOcrHttpParser


def _zip_with_md(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


@respx.mock
def test_monkeyocr_zip_contract(tmp_path):
    f = tmp_path / "doc.png"
    f.write_bytes(b"img")
    respx.post("http://mk.example/parse").mock(return_value=httpx.Response(
        200, json={"success": True, "download_url": "/static/out.zip"}))
    respx.get("http://mk.example/static/out.zip").mock(return_value=httpx.Response(
        200, content=_zip_with_md("out/doc.md", "# 表格内容")))
    udr = MonkeyOcrHttpParser(base_url="http://mk.example").parse(str(f))
    assert udr.parser == "monkeyocr" and udr.full_markdown == "# 表格内容"


@respx.mock
def test_rapidocr_polygon_to_bbox(tmp_path):
    f = tmp_path / "doc.png"
    f.write_bytes(b"img")
    respx.post("http://ro.example/ocr").mock(return_value=httpx.Response(200, json={
        "text": "发票 123",
        "pages": [{"page": 1, "items": [
            {"text": "发票 123", "score": 0.93,
             "box": [[10, 20], [110, 20], [110, 50], [10, 50]]}]}]}))
    udr = RapidOcrHttpParser(base_url="http://ro.example").parse(str(f))
    b = udr.pages[0].blocks[0]
    assert b.bbox == [10.0, 20.0, 110.0, 50.0] and b.confidence == 0.93


@respx.mock
def test_glm_ocr_sends_data_uri(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    f = tmp_path / "doc.png"
    f.write_bytes(b"img")
    route = respx.post("https://glm.example/layout_parsing").mock(
        return_value=httpx.Response(200, json={
            "data_info": {"pages": [{"width": 100, "height": 200}]},
            "layout_details": [[{"label": "text", "content": "hi",
                                 "bbox_2d": [1, 2, 3, 4]}]],
            "md_results": "hi"}))
    udr = GlmOcrCloudParser(base_url="https://glm.example").parse(str(f))
    body = route.calls[0].request.content
    assert b'"data:image/png;base64,' in body     # the 1214-pitfall contract
    assert udr.pages[0].blocks[0].bbox == [1, 2, 3, 4]


@respx.mock
def test_scan_chain_degrades_to_rapidocr(tmp_path, monkeypatch):
    """Cloud parser without key -> ParserUnavailable -> rapidocr fallback."""
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("RAPIDOCR_URL", "http://ro.example")
    f = tmp_path / "scan.jpg"
    f.write_bytes(b"img")
    respx.post("http://ro.example/ocr").mock(return_value=httpx.Response(200, json={
        "text": "fallback ok", "pages": [{"page": 1, "items": [
            {"text": "fallback ok", "score": 0.9,
             "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}]}))
    from app.parsers import router as r
    udr = r.parse_document(str(f))
    assert udr.parser == "rapidocr" and "fallback ok" in udr.full_markdown


def test_pdfplumber_char_alignment():
    """Glyph map must stay 1:1 with the assembled text or bail entirely —
    a misaligned map would draw redaction boxes on the wrong glyphs."""
    from app.parsers.electronic import _char_boxes
    chars = [{"text": "A", "x0": 0, "top": 0, "x1": 5, "bottom": 9},
             {"text": "B", "x0": 5, "top": 0, "x1": 10, "bottom": 9}]
    # assembler-inserted space -> None placeholder keeps alignment
    assert _char_boxes("A B", chars) == [
        [0.0, 0.0, 5.0, 9.0], None, [5.0, 0.0, 10.0, 9.0]]
    # desync (ligature/stripped glyph) bails to None -> line-bbox fallback
    assert _char_boxes("AX", chars) is None
    # trailing unconsumed glyphs (stripped text) also bail
    assert _char_boxes("A", chars) is None


def test_unsupported_type_raises(tmp_path):
    f = tmp_path / "x.xyz"
    f.write_bytes(b"?")
    from app.parsers import router as r
    with pytest.raises(ParserUnavailable):
        r.parse_document(str(f))
