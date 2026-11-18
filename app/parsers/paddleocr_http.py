"""PaddleOCR serving adapter (BOC POC 2026-11): scans and images go to a
customer-hosted PaddleOCR service, returning text lines WITH boxes — so
verification highlight and the masking channel work on scans too (the
multimodal route reads well but has no geometry).

Protocol: the official PaddleOCR 3.x / PaddleX serving API, per
PaddleOCR docs/version3.x/pipeline_usage/{OCR,PP-StructureV3,PaddleOCR-VL}.md
(read 2026-11-18):

  POST <endpoint>   {"file": <base64>, "fileType": 1, **extra_params}
  /ocr             -> result.ocrResults[0].prunedResult
                        {rec_texts, rec_scores, rec_polys | rec_boxes}      -> one block per line
  /layout-parsing  -> result.layoutParsingResults[0].prunedResult.parsing_res_list
                        [{block_label, block_content, block_bbox}]          -> one block per region
                      + markdown.text
  failure          -> errorCode != 0, errorMsg

The response shape decides how it is read, so the operator only pastes the
endpoint URL. Pages are rasterized here (detectors.render, 200 DPI) and sent
one PNG at a time: boxes then live in our own page pixel space, and the
service's default 10-page PDF cap never applies.

This module stays pure (no DB): the console-held settings — endpoint, token,
auth header — are decrypted by app/extraction/ocr_service.py and pushed in
through configure(); the task runner and app startup keep that cache warm.
"""
import base64
import io
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

log = logging.getLogger("idp.paddleocr")

_WORKERS = 4                       # pages are independent; same fan-out as the vlm parser
_active: dict | None = None        # runtime config incl. plaintext token; None = not in use


def configure(cfg: dict | None) -> None:
    """Install (or clear) the runtime config. Only enabled configs with an
    endpoint count — a disabled one must route scans elsewhere."""
    global _active
    _active = dict(cfg) if cfg and cfg.get("enabled") and cfg.get("url") else None


def active_config() -> dict | None:
    return _active


def _headers(cfg: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    token = (cfg.get("token") or "").strip()
    if token:
        headers[cfg.get("auth_header") or "Authorization"] = f"{cfg.get('auth_prefix', 'Bearer ')}{token}"
    return headers


def _bbox(raw) -> list[float] | None:
    """[x0,y0,x1,y1] from a 4-number box or a polygon of [x,y] points."""
    try:
        if raw and len(raw) == 4 and not isinstance(raw[0], (list, tuple)):
            return [float(v) for v in raw]
        xs = [float(p[0]) for p in raw]
        ys = [float(p[1]) for p in raw]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None


def _block_type(label: str) -> str:
    label = (label or "").lower()
    if label == "table":
        return "table"
    if label.endswith("title"):
        return "title"
    if label in ("image", "figure", "chart"):
        return "figure"
    return "text"


def read_response(data: dict) -> tuple[list[Block], str, str]:
    """One page of service output -> (blocks, page markdown, shape).
    Raises ParserUnavailable on a service-side error or an unknown shape."""
    if not isinstance(data, dict):
        raise ParserUnavailable("PaddleOCR 返回的不是 JSON 对象")
    code = data.get("errorCode", 0)
    if code not in (0, None):
        raise ParserUnavailable(f"PaddleOCR errorCode {code}: {str(data.get('errorMsg'))[:160]}")
    result = data.get("result") or {}

    if "ocrResults" in result:
        pr = ((result.get("ocrResults") or [{}])[0] or {}).get("prunedResult") or {}
        texts = pr.get("rec_texts") or []
        scores = pr.get("rec_scores") or []
        boxes = pr.get("rec_boxes") or []
        polys = pr.get("rec_polys") or []
        blocks = []
        for i, text in enumerate(texts):
            if not str(text).strip():
                continue
            raw = boxes[i] if i < len(boxes) else (polys[i] if i < len(polys) else None)
            score = scores[i] if i < len(scores) else None
            blocks.append(Block(text=str(text), bbox=_bbox(raw),
                                confidence=float(score) if score is not None else 1.0))
        return blocks, "\n".join(b.text for b in blocks), "ocr"

    if "layoutParsingResults" in result:
        lr = (result.get("layoutParsingResults") or [{}])[0] or {}
        pr = lr.get("prunedResult") or {}
        blocks = [Block(type=_block_type(r.get("block_label")), text=str(r.get("block_content") or ""),
                        bbox=_bbox(r.get("block_bbox")))
                  for r in pr.get("parsing_res_list") or []
                  if str(r.get("block_content") or "").strip()]
        markdown = (lr.get("markdown") or {}).get("text") or "\n\n".join(b.text for b in blocks)
        return blocks, markdown, "layout"

    raise ParserUnavailable("PaddleOCR 返回格式无法识别（没有 ocrResults 也没有 layoutParsingResults），"
                            "请确认接口地址是 /ocr 或 /layout-parsing")


def ocr_png(png: bytes, cfg: dict, client: httpx.Client) -> tuple[list[Block], str, str]:
    """Send one page image; transport and HTTP failures become ParserUnavailable
    (the router then falls back to the next scan engine)."""
    body = {"file": base64.b64encode(png).decode("ascii"), "fileType": 1,
            **(cfg.get("extra_params") or {})}
    try:
        resp = client.post(cfg["url"], json=body, headers=_headers(cfg))
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        raise ParserUnavailable(f"PaddleOCR 服务连不上：{e}") from e
    if resp.status_code != 200:
        raise ParserUnavailable(f"PaddleOCR HTTP {resp.status_code}: {resp.text[:160]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise ParserUnavailable(f"PaddleOCR 返回的不是 JSON：{resp.text[:160]}") from e
    return read_response(data)


def make_client(cfg: dict, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(timeout=float(cfg.get("timeout_s") or 120),
                        verify=bool(cfg.get("verify_tls", True)), transport=transport)


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@registry.register("parser", "paddleocr")
class PaddleOcrHttpParser:
    def __init__(self, cfg: dict | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.cfg = cfg if cfg is not None else active_config()
        self.transport = transport

    def parse(self, path: str) -> UDR:
        if not self.cfg:
            raise ParserUnavailable("PaddleOCR 未在后台启用")
        try:
            from app.detectors.render import rasterize
            images, total = rasterize(path)
        except Exception as e:                       # missing vision extra, unrenderable file
            raise ParserUnavailable(f"页面转图失败：{e}") from e
        with make_client(self.cfg, self.transport) as client, \
                ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            read = list(pool.map(lambda im: ocr_png(_png(im.image), self.cfg, client), images))
        pages = [Page(page_no=im.page_no, width=im.width, height=im.height,
                      blocks=blocks, markdown=md)
                 for im, (blocks, md, _shape) in zip(images, read)]
        name = "paddleocr"
        if len(images) < total:
            # provenance carries the truncation: a partial read must not look complete
            name = f"paddleocr(truncated {len(images)}/{total})"
            log.warning("paddleocr read %d of %d pages: %s", len(images), total, path)
        return UDR(pages=pages, parser=name,
                   full_markdown="\n\n".join(p.markdown for p in pages if p.markdown))
