"""9.15 WP5 fast mode: page rasterisation for vision channels (pypdfium2 —
fitz is banned, AGPL). Produces page_no -> PNG data-URI maps that the
extraction pipeline feeds straight to multimodal model calls, and image
dimensions for the minimal UDR (no OCR)."""
from __future__ import annotations

import base64
import logging

log = logging.getLogger("idp.raster")

_SCALE = 2.0        # 144 dpi-ish: readable numbers without huge payloads


def _to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def pdf_page_count(path: str) -> int:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def pdf_pages_to_images(path: str, max_pages: int) -> dict[int, str]:
    """Render at most `max_pages` pages -> {page_no: png data uri} (1-based)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        log.warning("pypdfium2 unavailable; vision fast mode disabled")
        return {}
    out: dict[int, str] = {}
    try:
        pdf = pdfium.PdfDocument(path)
        try:
            for i in range(min(len(pdf), max_pages)):
                page = pdf[i]
                bitmap = page.render(scale=_SCALE)
                pil = bitmap.to_pil()
                import io
                buf = io.BytesIO()
                pil.save(buf, format="PNG")
                out[i + 1] = _to_data_uri(buf.getvalue())
                page.close()
        finally:
            pdf.close()
    except Exception as e:
        log.warning("pdf rasterisation failed (%s); falling back", e)
        return {}
    return out


def pdf_page_sizes(path: str, max_pages: int) -> list[tuple[float, float]]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    sizes = []
    try:
        pdf = pdfium.PdfDocument(path)
        try:
            for i in range(min(len(pdf), max_pages)):
                page = pdf[i]
                sizes.append((page.get_width(), page.get_height()))
                page.close()
        finally:
            pdf.close()
    except Exception:
        return []
    return sizes


def image_size(path: str) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def image_to_data_uri(path: str) -> str:
    with open(path, "rb") as fh:
        return _to_data_uri(fh.read())


def has_text_layer(path: str) -> bool:
    """Cheap text-layer probe: pdfplumber over the first pages; >40 chars of
    aggregated text means an electronic PDF."""
    try:
        import pdfplumber
    except ImportError:
        return False
    try:
        with pdfplumber.open(path) as pdf:
            text = ""
            for page in pdf.pages[:3]:
                text += page.extract_text() or ""
            return len(text.strip()) > 40
    except Exception:
        return False
