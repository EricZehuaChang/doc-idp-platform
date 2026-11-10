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
from tempfile import TemporaryDirectory

import httpx

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".bmp": "image/bmp", ".webp": "image/webp", ".pdf": "application/pdf",
}


# Base64 expands the body by one third. Bound each upload below 16 MiB to
# avoid transport write timeouts when a high-resolution scan approaches the
# provider's 50 MB file ceiling.
_PDF_MAX_BYTES = 12 * 1024 * 1024
_PDF_MAX_PAGES = 100


def _pdf_parts(path: Path, directory: Path) -> list[tuple[Path, int, int]]:
    """Preflight all page ranges before issuing the first billable OCR request.

    Return (file, first original page number, count) in reading order. Split
    oversized ranges by halves and copy PDF page objects without rasterizing.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.errors import PyPdfError
    except ImportError as exc:
        raise ParserUnavailable("glm-ocr PDF input requires pypdf") from exc

    parts = []
    try:
        with path.open("rb") as source:
            reader = PdfReader(source)
            total = len(reader.pages)
            if total == 0:
                raise ParserUnavailable("glm-ocr: PDF has no pages")
            original_fits = total <= _PDF_MAX_PAGES and path.stat().st_size <= _PDF_MAX_BYTES
            if original_fits:
                return [(path, 1, total)]

            # A stack avoids keeping parent PDF writers alive while splitting
            # children. Right goes on first, so left pages are emitted first.
            ranges = [(0, total)]
            while ranges:
                start, stop = ranges.pop()
                count = stop - start
                # The original file's size is already known. Recheck a
                # single-page source after removing non-page document data.
                whole_file = count == total and count > 1
                if count <= _PDF_MAX_PAGES and not whole_file:
                    candidate = directory / f"pages-{start + 1}-{stop}.pdf"
                    writer = PdfWriter()
                    for index in range(start, stop):
                        writer.add_page(reader.pages[index])
                    with candidate.open("wb") as output:
                        writer.write(output)
                    if candidate.stat().st_size <= _PDF_MAX_BYTES:
                        parts.append((candidate, start + 1, count))
                        continue
                    candidate.unlink()
                    if count == 1:
                        raise ParserUnavailable(
                            f"glm-ocr: PDF page {start + 1} exceeds the single-request "
                            f"limit of {_PDF_MAX_BYTES} bytes")
                middle = start + count // 2
                ranges.extend(((middle, stop), (start, middle)))
    except (OSError, PyPdfError, ValueError) as exc:
        raise ParserUnavailable(f"glm-ocr cannot read or split PDF: {exc}") from exc
    return parts


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
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            if mime != "application/pdf":
                return self._request(client, p, mime, key)
            with TemporaryDirectory(prefix="idp-glm-pdf-") as directory:
                parts = _pdf_parts(p, Path(directory))
                if len(parts) == 1:
                    file, first, count = parts[0]
                    return self._request(client, file, mime, key, first, count)
                pages = []
                texts = []
                for file, first, count in parts:
                    part = self._request(client, file, mime, key, first, count)
                    pages.extend(part.pages)
                    # If one chunk has only layout text, it must not disappear
                    # simply because another chunk supplied Markdown.
                    markdown = part.full_markdown
                    if not markdown:
                        page_texts = ["\n".join(b.text for b in page.blocks if b.text) for page in part.pages]
                        markdown = "\n\n".join(page_texts) if any(page_texts) else ""
                    texts.append(markdown)
                return UDR(pages=pages, full_markdown="\n\n".join(texts) if any(texts) else "",
                           parser="glm-ocr-cloud")

    def _request(self, client: httpx.Client, path: Path, mime: str, key: str,
                 first: int = 1, expected: int | None = None) -> UDR:
        data_uri = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()
        try:
            resp = client.post(
                f"{self.base_url}/layout_parsing",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "glm-ocr", "file": data_uri})
            resp.raise_for_status()
        except httpx.RequestError as exc:
            raise ParserUnavailable(f"glm-ocr unreachable: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ParserUnavailable(
                f"glm-ocr HTTP {exc.response.status_code}: {exc.response.text[:160]}") from exc

        try:
            data = resp.json()
            info = data.get("data_info") or {}
            pages_info = info.get("pages")
            layout = data.get("layout_details")
            # GLM returns one list of blocks per page. A blank page is an empty
            # list; a missing list cannot be treated as a successful blank page.
            if expected is not None:
                if (not isinstance(layout, list) or len(layout) != expected
                        or any(not isinstance(blocks, list) for blocks in layout)):
                    raise ParserUnavailable(
                        f"glm-ocr incomplete PDF layout for pages {first}-{first + expected - 1}: "
                        f"expected {expected} page lists")
                count = info.get("num_pages", expected)
                if (type(count) is not int or count != expected or
                        (pages_info not in (None, []) and
                         (not isinstance(pages_info, list) or len(pages_info) != expected))):
                    raise ParserUnavailable(f"glm-ocr PDF page metadata disagrees with {expected} input pages")
            pages_info = pages_info or []
            pages = []
            for i, page_blocks in enumerate(layout or []):
                page_info = pages_info[i] if i < len(pages_info) else {}
                blocks = [Block(type=b.get("label", "text"), text=b.get("content", "") or "",
                                bbox=b.get("bbox_2d")) for b in (page_blocks or [])]
                pages.append(Page(page_no=first + i, width=page_info.get("width", 0),
                                  height=page_info.get("height", 0), blocks=blocks))
            return UDR(pages=pages, full_markdown=data.get("md_results", "") or "", parser="glm-ocr-cloud")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ParserUnavailable("glm-ocr returned an invalid layout response") from exc
