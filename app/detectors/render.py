"""Page rasterization for visual detectors (design 2026-08-07 §2.2).
PDF pages -> RGB images via pypdfium2 (PDFium, BSD/Apache-2.0). PyMuPDF/fitz
is BANNED in this repo (AGPL, see pyproject.toml) — this module is the only
place a rasterizer is allowed, keep it pypdfium2. Plain images load through
Pillow directly. The returned PageImage width/height define the page-pixel
coordinate space every Region bbox is expressed in (per page — pages may
render at different effective DPI when the bomb guard downscales one).

Resource guards (2026-08-07 adversarial review): a PDF's declared MediaBox is
attacker-controlled and the upload-size cap does not bound raster size — a
300-byte file can declare a 14400x14400pt page (4.8GB RGB at 200 DPI). Guards:
per-page pixel cap (oversized pages downscale, detection quality is unaffected
because detectors downscale anyway) and a per-document pixel budget (exceeding
it is an explicit DetectorUnavailable -> 422, never an OOM/500).
"""
from pathlib import Path

from app.detectors.base import DetectorUnavailable, PageImage

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
_DPI = 200                        # detection-grade raster (design §2.2)
_MAX_PAGES = 100                  # same page ceiling as the GLM-OCR cloud contract
_MAX_PAGE_PIXELS = 24_000_000     # ~A2 at 300 DPI; larger pages render at lower DPI
_MAX_TOTAL_PIXELS = 400_000_000   # ~100 A4 pages at 200 DPI (~1.1GB RGB) per request


def rasterize(path: str) -> tuple[list[PageImage], int]:
    """File -> (RGB page images, total pages in the document). The list stops
    at _MAX_PAGES; callers MUST surface len(pages) < total as an explicit
    truncation signal — a silent partial scan would read as "no seal found"
    downstream, which the redaction channel cannot afford. Raises
    DetectorUnavailable when the vision extra is missing or the file cannot
    be rendered within the resource budget (API turns that into a 422)."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return [_load_image(p)], 1
    if suffix == ".pdf":
        return _rasterize_pdf(p)
    raise DetectorUnavailable(f"unsupported suffix for detection: {suffix}")


def _load_image(p: Path) -> PageImage:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - vision extra missing
        raise DetectorUnavailable("pillow not installed — pip install '.[vision]'") from e
    try:
        # Image.open enforces PIL's MAX_IMAGE_PIXELS decompression-bomb guard;
        # that error (and any decode error) degrades to the explicit 422 path
        img = Image.open(p)
        img = img.convert("RGB")
    except Exception as e:
        raise DetectorUnavailable(f"cannot read image: {e}") from e
    return PageImage(page_no=1, image=img,
                     width=float(img.width), height=float(img.height))


def _rasterize_pdf(p: Path) -> tuple[list[PageImage], int]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # pragma: no cover - vision extra missing
        raise DetectorUnavailable("pypdfium2 not installed — pip install '.[vision]'") from e
    try:
        doc = pdfium.PdfDocument(str(p))
    except Exception as e:
        raise DetectorUnavailable(f"cannot open pdf: {e}") from e
    pages: list[PageImage] = []
    budget = _MAX_TOTAL_PIXELS
    try:
        total = len(doc)
        for i in range(min(total, _MAX_PAGES)):
            page = doc[i]
            try:
                w_pt, h_pt = page.get_size()
                scale = _DPI / 72
                if w_pt * h_pt * scale * scale > _MAX_PAGE_PIXELS:
                    # oversized MediaBox: render at whatever DPI fits the cap
                    scale = (_MAX_PAGE_PIXELS / (w_pt * h_pt)) ** 0.5
                budget -= int(w_pt * scale) * int(h_pt * scale)
                if budget < 0:
                    raise DetectorUnavailable(
                        f"文档栅格总量超限(第 {i + 1} 页起超出预算),请拆分文档后重试")
                bitmap = page.render(scale=scale)
                img = bitmap.to_pil().convert("RGB")
            except DetectorUnavailable:
                raise
            except Exception as e:
                # includes MemoryError/PdfiumError: explicit 422, never a 500
                raise DetectorUnavailable(f"cannot rasterize page {i + 1}: {e}") from e
            finally:
                page.close()
            pages.append(PageImage(page_no=i + 1, image=img,
                                   width=float(img.width), height=float(img.height)))
    finally:
        doc.close()
    if not pages:
        raise DetectorUnavailable("pdf has no renderable pages")
    return pages, total
