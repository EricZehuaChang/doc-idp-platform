"""RapidOCR CPU fallback parser (design v0.2 §4.2): adapter for the existing
ocr-service-standalone microservice (D:/Claude Code/ocr-service-standalone,
POST /ocr multipart -> pages[].items[{text, score, box(4-point polygon)}]).
Role: zero-GPU last resort when no cloud key and no local GPU model — quality
below GLM-OCR, so confidence scores flow through for the review gate.
"""
import os
from pathlib import Path

import httpx

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry


def _poly_to_bbox(box) -> list[float] | None:
    """4-point polygon [[x,y]x4] -> [x0,y0,x1,y1]."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None


@registry.register("parser", "rapidocr")
class RapidOcrHttpParser:
    def __init__(self, base_url: str | None = None, timeout: float = 300.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.environ.get(
            "RAPIDOCR_URL", "http://127.0.0.1:31881")).rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def parse(self, path: str) -> UDR:
        p = Path(path)
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(
                    f"{self.base_url}/ocr",
                    files={"file": (p.name, p.read_bytes())},
                    data={"ocr_type": "general"})
                resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ParserUnavailable(f"rapidocr service unreachable: {e}") from e
        except httpx.HTTPStatusError as e:
            raise ParserUnavailable(
                f"rapidocr HTTP {e.response.status_code}: {e.response.text[:160]}") from e

        data = resp.json()
        pages: list[Page] = []
        for pg in data.get("pages", []):
            blocks = [Block(text=item.get("text", ""),
                            bbox=_poly_to_bbox(item.get("box")),
                            confidence=float(item["score"]) if item.get("score") else 1.0)
                      for item in pg.get("items", [])]
            pages.append(Page(page_no=int(pg.get("page", 1)), blocks=blocks))
        return UDR(pages=pages, full_markdown=data.get("text", "") or "",
                   parser="rapidocr")
