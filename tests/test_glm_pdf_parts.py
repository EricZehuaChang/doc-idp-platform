"""PDF-to-GLM limits, page fidelity and all-or-nothing error handling."""
import base64
import hashlib
import json
import tempfile
from io import BytesIO

import httpx
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.parsers import glm_ocr_cloud as glm
from app.parsers.base import ParserUnavailable


def make_pdf(path, page_sizes):
    """Unique media boxes and streams identify which original page was copied."""
    writer = PdfWriter()
    for i, size in enumerate(page_sizes, 1):
        page = writer.add_blank_page(width=72 + i, height=144 + i)
        stream = DecodedStreamObject()
        stream.set_data(b"% page " + str(i).encode() + b"\n" + b" " * size)
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def original_pages(raw):
    pages = PdfReader(BytesIO(raw)).pages
    numbers = [int(p.mediabox.width) - 72 for p in pages]
    for number, page in zip(numbers, pages):
        assert int(page.mediabox.height) == 144 + number
        assert page.get_contents().get_data().startswith(f"% page {number}\n".encode())
    return numbers


def response(numbers):
    return {"data_info": {"num_pages": len(numbers),
                          "pages": [{"width": 720 + n, "height": 1440 + n} for n in numbers]},
            "layout_details": [[{"label": "text", "content": f"段落{n}",
                                 "bbox_2d": [n, 2, 10, 12]}] for n in numbers],
            "md_results": "\n\n".join(f"## 第{n}页  \n正文{n}" for n in numbers)}


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setenv("GLM_FIXTURE_KEY", "fixture")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    def run(path, reply):
        def handle(request):
            assert request.url.path == "/layout_parsing"
            body = json.loads(request.content)
            assert body["model"] == "glm-ocr"
            prefix, encoded = body["file"].split(",", 1)
            assert prefix == "data:application/pdf;base64"
            raw = base64.b64decode(encoded, validate=True)
            assert len(raw) <= glm._PDF_MAX_BYTES
            numbers = original_pages(raw)
            assert 0 < len(numbers) <= glm._PDF_MAX_PAGES
            return reply(raw, numbers)

        parser = glm.GlmOcrCloudParser(base_url="https://ocr.invalid", api_key_env="GLM_FIXTURE_KEY",
                                       transport=httpx.MockTransport(handle))
        before = hashlib.sha256(path.read_bytes()).digest()
        try:
            return parser.parse(str(path))
        finally:
            assert before == hashlib.sha256(path.read_bytes()).digest()
            assert list(scratch.iterdir()) == []

    return run


def test_small_pdf_sends_original_bytes_and_verbatim_markdown(tmp_path, service):
    path = make_pdf(tmp_path / "scan.pdf", [12, 20])

    def reply(raw, numbers):
        assert raw == path.read_bytes() and numbers == [1, 2]
        return httpx.Response(200, json={**response(numbers), "md_results": "# 标题  \n\n<table/>\n"})

    assert service(path, reply).full_markdown == "# 标题  \n\n<table/>\n"


@pytest.mark.parametrize("byte_limit,page_limit", [(3_000, 100), (50 * 1024**2, 3)])
def test_splits_limits_and_preserves_every_page(tmp_path, service, monkeypatch, byte_limit, page_limit):
    path = make_pdf(tmp_path / "scan.pdf", [900] * 7)
    monkeypatch.setattr(glm, "_PDF_MAX_BYTES", byte_limit)
    monkeypatch.setattr(glm, "_PDF_MAX_PAGES", page_limit)
    batches = []

    def reply(raw, numbers):
        batches.append(numbers)
        return httpx.Response(200, json=response(numbers))

    document = service(path, reply)
    assert len(batches) > 1 and [n for batch in batches for n in batch] == list(range(1, 8))
    assert document.full_markdown == "\n\n".join(f"## 第{n}页  \n正文{n}" for n in range(1, 8))
    assert document.parser == "glm-ocr-cloud" and len(document.pages) == 7
    for n, page in enumerate(document.pages, 1):
        assert (page.page_no, page.width, page.height) == (n, 720 + n, 1440 + n)
        assert page.blocks[0].text == f"段落{n}" and page.blocks[0].bbox == [n, 2, 10, 12]


@pytest.mark.parametrize("bad", ["http", "missing", "short", "excess", "null_page", "metadata", "num_pages"])
def test_incomplete_later_chunk_fails_without_partial_result(tmp_path, service, monkeypatch, bad):
    path = make_pdf(tmp_path / "scan.pdf", [100] * 4)
    monkeypatch.setattr(glm, "_PDF_MAX_PAGES", 2)
    calls = []

    def reply(raw, numbers):
        calls.append(numbers)
        payload = response(numbers)
        if len(calls) == 2:
            if bad == "http":
                return httpx.Response(503, text="unavailable")
            if bad == "missing":
                del payload["layout_details"]
            if bad == "short":
                payload["layout_details"].pop()
            if bad == "excess":
                payload["layout_details"].append([])
            if bad == "null_page":
                payload["layout_details"][1] = None
            if bad == "metadata":
                payload["data_info"]["pages"].pop()
            if bad == "num_pages":
                payload["data_info"]["num_pages"] = 1
        return httpx.Response(200, json=payload)

    with pytest.raises(ParserUnavailable):
        service(path, reply)
    assert calls == [[1, 2], [3, 4]]


def test_oversize_individual_page_fails_before_any_request(tmp_path, service, monkeypatch):
    path = make_pdf(tmp_path / "scan.pdf", [150, 5000])
    monkeypatch.setattr(glm, "_PDF_MAX_BYTES", 2000)
    with pytest.raises(ParserUnavailable, match="PDF page 2 exceeds"):
        service(path, lambda *args: pytest.fail("no GLM request for an unsendable document"))


def test_chunk_with_only_layout_text_is_present_in_markdown(tmp_path, service, monkeypatch):
    path = make_pdf(tmp_path / "scan.pdf", [120, 120])
    monkeypatch.setattr(glm, "_PDF_MAX_PAGES", 1)

    def reply(raw, numbers):
        payload = response(numbers)
        if numbers == [2]:
            payload.pop("md_results")
        return httpx.Response(200, json=payload)

    assert service(path, reply).full_markdown == "## 第1页  \n正文1\n\n段落2"


def test_complete_small_pdf_accepts_empty_optional_dimensions(tmp_path, service):
    path = make_pdf(tmp_path / "one.pdf", [100])

    def reply(raw, numbers):
        payload = response(numbers)
        payload["data_info"]["pages"] = []
        return httpx.Response(200, json=payload)

    result = service(path, reply)
    assert result.full_markdown == "## 第1页  \n正文1"
    assert [(p.page_no, p.width, p.height) for p in result.pages] == [(1, 0, 0)]
