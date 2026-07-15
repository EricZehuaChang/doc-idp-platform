"""GLM-OCR cloud parser via Zhipu /layout_parsing (design v0.2 §4.2: cloud-first
parser for the lite tier; same model runs locally on vLLM in standard tier —
"cloud/local same source" is the decisive selection reason).

Adapted fork-and-own from KBase m5-1 kbase/plugins/ocr/glm_http.py, changed to
emit UDR (layout_details -> blocks with bbox) instead of markdown-only.

Live-verified contract note (2026-07-15, also recorded in KBase): the "file"
field MUST be a data URI ("data:{mime};base64,...") — raw base64 gets HTTP 400
code 1214 whose message misleadingly claims unsupported format.
Limits: images <=10MB, PDF <=50MB / <=100 pages.
"""
import base64
import os
from pathlib import Path

import httpx

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".bmp": "image/bmp", ".webp": "image/webp", ".pdf": "application/pdf",
}


@registry.register("parser", "glm-ocr-cloud")
class GlmOcrCloudParser:
    def __init__(self, base_url: str = "https://open.bigmodel.cn/api/paas/v4",
                 api_key_env: str = "ZHIPU_API_KEY", timeout: float = 180.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.transport = transport

    def parse(self, path: str) -> UDR:
        p = Path(path)
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            # key missing is a deployment issue, not a code path: retryable
            raise ParserUnavailable(f"{self.api_key_env} not configured")
        mime = _MIME.get(p.suffix.lower())
        if mime is None:
            raise ParserUnavailable(f"unsupported suffix for glm-ocr: {p.suffix}")
        data_uri = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(
                    f"{self.base_url}/layout_parsing",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "glm-ocr", "file": data_uri})
                resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ParserUnavailable(f"glm-ocr unreachable: {e}") from e
        except httpx.HTTPStatusError as e:
            raise ParserUnavailable(
                f"glm-ocr HTTP {e.response.status_code}: {e.response.text[:160]}") from e

        data = resp.json()
        pages_info = (data.get("data_info") or {}).get("pages") or []
        layout = data.get("layout_details") or []
        pages: list[Page] = []
        for i, page_blocks in enumerate(layout):
            info = pages_info[i] if i < len(pages_info) else {}
            blocks = [Block(type=b.get("label", "text"),
                            text=b.get("content", "") or "",
                            bbox=b.get("bbox_2d"))
                      for b in (page_blocks or [])]
            pages.append(Page(page_no=i + 1, width=info.get("width", 0),
                              height=info.get("height", 0), blocks=blocks))
        return UDR(pages=pages, full_markdown=data.get("md_results", "") or "",
                   parser="glm-ocr-cloud")
