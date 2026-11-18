"""PaddleOCR serving adapter + console settings (BOC POC 2026-11).

Response fixtures follow the official serving docs (PaddleOCR
docs/version3.x/pipeline_usage/{OCR,PP-StructureV3}.md): /ocr answers
result.ocrResults[].prunedResult, /layout-parsing answers
result.layoutParsingResults[].prunedResult.parsing_res_list."""
import json

import httpx
import pytest
import respx
from PIL import Image
from reportlab.pdfgen import canvas

from app.parsers import paddleocr_http, router
from app.parsers.base import ParserUnavailable
from tests.test_wp5_fast_and_playground import _boot

URL = "http://paddle.test:8080/ocr"

OCR_OK = {"logId": "x", "errorCode": 0, "errorMsg": "Success", "result": {"ocrResults": [{
    "prunedResult": {"rec_texts": ["发票号码 INV-1", "", "合计 100"],
                     "rec_scores": [0.98, 0.1, 0.91],
                     "rec_polys": [[[10, 20], [200, 20], [200, 50], [10, 50]],
                                   [[0, 0], [1, 0], [1, 1], [0, 1]],
                                   [[12, 70], [150, 70], [150, 98], [12, 98]]],
                     "rec_boxes": [[10, 20, 200, 50], [0, 0, 1, 1], [12, 70, 150, 98]]}}],
    "dataInfo": {}}}
LAYOUT_OK = {"errorCode": 0, "errorMsg": "Success", "result": {"layoutParsingResults": [{
    "prunedResult": {"parsing_res_list": [
        {"block_label": "doc_title", "block_content": "采购合同", "block_bbox": [30, 10, 400, 60]},
        {"block_label": "table", "block_content": "<table><tr><td>A</td></tr></table>",
         "block_bbox": [30, 100, 500, 300]},
        {"block_label": "image", "block_content": "", "block_bbox": [0, 0, 5, 5]}]},
    "markdown": {"text": "# 采购合同\n\n<table>...</table>"}}]}}


@pytest.fixture(autouse=True)
def _reset():
    paddleocr_http.configure(None)
    yield
    paddleocr_http.configure(None)


def _cfg(**over):
    return {"enabled": True, "url": URL, "token": "tok-1", "auth_header": "Authorization",
            "auth_prefix": "Bearer ", "timeout_s": 30, "verify_tls": True,
            "extra_params": {}, **over}


def test_ocr_shape_gives_one_block_per_line_with_boxes():
    blocks, md, shape = paddleocr_http.read_response(OCR_OK)
    assert shape == "ocr"
    assert [b.text for b in blocks] == ["发票号码 INV-1", "合计 100"]      # blank line dropped
    assert blocks[0].bbox == [10.0, 20.0, 200.0, 50.0]
    assert blocks[1].confidence == pytest.approx(0.91)
    assert md == "发票号码 INV-1\n合计 100"


def test_ocr_shape_falls_back_to_polygons_without_rec_boxes():
    data = json.loads(json.dumps(OCR_OK))
    del data["result"]["ocrResults"][0]["prunedResult"]["rec_boxes"]
    blocks, _, _ = paddleocr_http.read_response(data)
    assert blocks[1].bbox == [12.0, 70.0, 150.0, 98.0]


def test_layout_shape_keeps_region_types_and_markdown():
    blocks, md, shape = paddleocr_http.read_response(LAYOUT_OK)
    assert shape == "layout"
    assert [(b.type, b.text[:4]) for b in blocks] == [("title", "采购合同"), ("table", "<tab")]
    assert blocks[1].bbox == [30.0, 100.0, 500.0, 300.0]
    assert md.startswith("# 采购合同")


@pytest.mark.parametrize("data,msg", [
    ({"errorCode": 422, "errorMsg": "Invalid file"}, "errorCode 422"),
    ({"errorCode": 0, "result": {"something": []}}, "无法识别"),
    (["not", "a", "dict"], "不是 JSON 对象"),
])
def test_service_errors_become_parser_unavailable(data, msg):
    with pytest.raises(ParserUnavailable, match=msg):
        paddleocr_http.read_response(data)


@respx.mock
def test_parser_sends_each_pdf_page_as_an_image_with_the_token(tmp_path):
    pdf = tmp_path / "two.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(72, 720, "page one")
    c.showPage()
    c.drawString(72, 720, "page two")
    c.save()
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OCR_OK))
    cfg = _cfg(auth_header="X-Api-Key", auth_prefix="", extra_params={"visualize": False})
    udr = paddleocr_http.PaddleOcrHttpParser(cfg=cfg).parse(str(pdf))
    assert route.call_count == 2
    req = route.calls[0].request
    assert req.headers["X-Api-Key"] == "tok-1"
    assert "Authorization" not in req.headers
    body = json.loads(req.content)
    assert body["fileType"] == 1 and body["visualize"] is False and len(body["file"]) > 100
    assert udr.parser == "paddleocr"
    assert [p.page_no for p in udr.pages] == [1, 2]
    assert udr.pages[0].width > 0 and udr.pages[0].blocks[0].bbox == [10.0, 20.0, 200.0, 50.0]
    assert "合计 100" in udr.full_text()


@respx.mock
def test_parser_reports_http_failures_as_unavailable(tmp_path):
    img = tmp_path / "scan.png"
    Image.new("RGB", (100, 60), "white").save(img)
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad token"))
    with pytest.raises(ParserUnavailable, match="HTTP 401"):
        paddleocr_http.PaddleOcrHttpParser(cfg=_cfg()).parse(str(img))
    with pytest.raises(ParserUnavailable, match="未在后台启用"):
        paddleocr_http.PaddleOcrHttpParser(cfg=None).parse(str(img))


def test_scan_chain_puts_enabled_service_first_and_falls_back(monkeypatch, tmp_path):
    assert router.scan_chain()[0] != "paddleocr"                 # not configured: unchanged
    paddleocr_http.configure(_cfg(enabled=False))
    assert "paddleocr" not in router.scan_chain()                 # disabled: ignored
    paddleocr_http.configure(_cfg())
    chain = router.scan_chain()
    assert chain[0] == "paddleocr" and chain[-1] == "rapidocr"

    calls = []

    class Down:
        def __init__(self, name): self.name = name

        def parse(self, path):
            calls.append(self.name)
            if self.name == "paddleocr":
                raise ParserUnavailable("down")
            return router.UDR(parser=self.name)

    monkeypatch.setattr(router, "_make", Down)
    assert router._scan_parse(str(tmp_path / "x.png")).parser == chain[1]
    assert calls == ["paddleocr", chain[1]]


async def test_console_settings_round_trip_and_warm(tmp_path, monkeypatch):
    async for client, _ in _boot(tmp_path, monkeypatch):
        r = await client.get("/api/v1/settings/ocr-service")
        assert r.status_code == 200 and r.json()["active"] is False

        bad = await client.put("/api/v1/settings/ocr-service", json={"enabled": True, "url": ""})
        assert bad.status_code == 400
        bad = await client.put("/api/v1/settings/ocr-service",
                               json={"url": URL, "extra_params": {"file": "x"}})
        assert bad.status_code == 400

        r = await client.put("/api/v1/settings/ocr-service",
                             json={"enabled": True, "url": URL, "token": "secret-tok"})
        view = r.json()
        assert r.status_code == 200 and view["has_token"] and view["active"]
        assert "secret-tok" not in r.text and "token_enc" not in view
        # saving warmed the parser cache: the next scan already routes there
        assert paddleocr_http.active_config()["token"] == "secret-tok"
        assert router.scan_chain()[0] == "paddleocr"

        r = await client.put("/api/v1/settings/ocr-service",
                             json={"enabled": True, "url": URL, "timeout_s": 60})
        assert r.json()["has_token"]                                  # blank token keeps it
        assert paddleocr_http.active_config()["token"] == "secret-tok"

        r = await client.put("/api/v1/settings/ocr-service",
                             json={"enabled": False, "url": URL, "clear_token": True})
        assert not r.json()["has_token"] and paddleocr_http.active_config() is None


async def test_console_test_button_makes_one_real_call(tmp_path, monkeypatch):
    async for client, _ in _boot(tmp_path, monkeypatch):
        await client.put("/api/v1/settings/ocr-service", json={"url": URL, "token": "stored"})
        hit = {"errorCode": 0, "result": {"ocrResults": [{"prunedResult": {
            "rec_texts": ["OCR TEST 73915"], "rec_scores": [0.99],
            "rec_boxes": [[50, 80, 700, 150]]}}]}}
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(URL).mock(return_value=httpx.Response(200, json=hit))
            mock.route(host="test").pass_through()
            r = await client.post("/api/v1/settings/ocr-service/test", json={"url": URL})
            assert r.status_code == 200 and r.json()["ok"] and r.json()["has_boxes"]
            # blank token in the form falls back to the stored one
            assert route.calls[0].request.headers["Authorization"] == "Bearer stored"

            mock.post(URL).mock(return_value=httpx.Response(500, text="boom"))
            r = await client.post("/api/v1/settings/ocr-service/test", json={"url": URL})
            assert r.status_code == 200 and not r.json()["ok"] and "HTTP 500" in r.json()["error"]
