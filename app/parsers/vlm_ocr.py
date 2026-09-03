"""VLM parser: page raster -> multimodal model -> Markdown (design v0.2 §4.2
`cloud_vlm` parser type, filled in 2026-08-27).

Why this exists alongside glm-ocr-cloud: a dedicated OCR endpoint returns
layout blocks with bbox but reads a page as text; a general multimodal model has
no bbox but understands the page — merged cells, stamps over text, handwriting,
rotated tables, forms whose meaning lives in the layout. Which one wins is
document-dependent, so this is a pinnable alternative, not a replacement, and
auto-routing is deliberately left untouched (existing API integrations keep
byte-identical behaviour).

Trade-off, stated plainly: **no bbox**. Every block comes back with bbox=None,
so verification highlight and the masking channel's `$hits` have nothing to
anchor to for a document parsed this way. Pin it for extraction quality on hard
layouts, not for anything that needs coordinates.
"""
import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import load_providers
from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

# A page raster per call: a long document is a long bill and a long wait. The
# cap is honest rather than silent — what was read is recorded in UDR.parser.
_MAX_PAGES = 20
_WORKERS = 4                       # pages are independent; 20 x 5s serial hurts

_PROMPT = (
    "把这一页文档完整转写为 Markdown。要求：\n"
    "1. 逐字转写页面上的文字，不要翻译、不要改写、不要总结、不要补充页面上没有的内容；\n"
    "2. 表格用 Markdown 表格还原，合并单元格按视觉归属展开；\n"
    "3. 标题用 #，保持阅读顺序；\n"
    "4. 印章、签名、手写批注等非印刷内容，用 `[印章: 文字]`、`[签名: 文字]`、"
    "`[手写: 文字]` 标注，文字辨认不出就写 `[印章: 不可辨认]`，不要猜；\n"
    "5. 只输出 Markdown 正文，不要任何解释或代码围栏。"
)


@registry.register("parser", "vlm-qwen")
@registry.register("parser", "vlm-deepseek")
@registry.register("parser", "vlm-glm")
@registry.register("parser", "vlm-deepseek-pro")
class VlmOcrParser:
    """One class, three registered names. Which model each name uses comes from
    configs/parsers.yaml (`provider:`), resolved against providers.yaml — so
    swapping the model behind a parser is a config edit, not a release."""

    def __init__(self, provider: str = "", timeout: float = 180.0,
                 transport: httpx.BaseTransport | None = None):
        self.provider = provider
        self.timeout = timeout
        self.transport = transport

    def parse(self, path: str) -> UDR:
        cfg = load_providers()["providers"].get(self.provider)
        if cfg is None:
            raise ParserUnavailable(f"vlm parser provider not configured: {self.provider}")
        key = os.environ.get(cfg.api_key_env, "").strip() if cfg.api_key_env else ""
        if cfg.api_key_env and not key:
            # deployment issue, not a bad document: retryable, same as the
            # other cloud parsers
            raise ParserUnavailable(f"{cfg.api_key_env} not configured")

        try:
            from app.detectors.render import rasterize
            images, total_pages = rasterize(path)
        except Exception as e:                     # missing vision extra, or unrenderable
            raise ParserUnavailable(f"vlm parser cannot rasterize {path}: {e}") from e
        if not images:
            raise ParserUnavailable("vlm parser: document produced no pages")
        used = images[:_MAX_PAGES]

        def one(page) -> tuple[int, str]:
            return page.page_no, self._transcribe(cfg, key, page.image)

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            texts = dict(pool.map(one, used))

        pages = [Page(page_no=p.page_no, width=p.width, height=p.height,
                      markdown=texts.get(p.page_no, ""),
                      # no bbox: a chat model returns text, not geometry
                      blocks=[Block(type="text", text=texts.get(p.page_no, ""),
                                    bbox=None)])
                 for p in used]
        # provenance carries the truncation — a silent partial read would look
        # like a short document to everything downstream
        name = self.provider
        if len(used) < total_pages:
            name = f"{name}(truncated {len(used)}/{total_pages})"
        return UDR(pages=pages, parser=f"vlm:{name}",
                   full_markdown="\n\n".join(p.markdown for p in pages))

    def _transcribe(self, cfg, key: str, image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        body = {
            "model": cfg.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": uri}}]}],
        }
        body.update(dict(cfg.extra_body or {}))
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(f"{cfg.base_url.rstrip('/')}/chat/completions",
                                   headers=headers, json=body)
                resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ParserUnavailable(f"vlm {self.provider} unreachable: {e}") from e
        except httpx.HTTPStatusError as e:
            raise ParserUnavailable(
                f"vlm {self.provider} HTTP {e.response.status_code}: "
                f"{e.response.text[:160]}") from e
        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ParserUnavailable(f"vlm {self.provider} malformed response") from e
        return _strip_fence(text.strip())


def _strip_fence(text: str) -> str:
    """Models wrap Markdown in ```markdown fences despite being told not to."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    return text
