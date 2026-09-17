"""9.15 WP6 (R12): searchable-PDF generation — page image as background plus
an invisible text layer (render mode 3) from UDR block coordinates.

License discipline (§WP6): ReportLab (BSD) only; PyMuPDF/Ghostscript/
OCRmyPDF (AGPL) are banned. CJK text uses ReportLab's built-in CID fonts, so
no font file is embedded or shipped.

Coordinate contract: UDR blocks are top-left origin with parser-dependent
units (PDF points for pdfplumber, render pixels for OCR). Everything is
normalised by the UDR page size first, then mapped onto the PDF page
(bottom-left origin, points) — so a wrong unit ratio cancels out as long as
UDR and page geometry come from the same parser run.
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger("idp.searchable")

_CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF),          # CJK unified
    (0x3040, 0x30FF), (0x31F0, 0x31FF),          # hiragana/katakana
    (0xAC00, 0xD7AF),                            # hangul
)


def _font_for(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                if 0xAC00 <= cp <= 0xD7AF:
                    return "HYSMyeongJo-Medium"
                if 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
                    return "HeiseiMin-W3"
                return "STSong-Light"
    return "Helvetica"


def _has_coords(udr) -> bool:
    """markitdown-style parsers emit no bboxes — nothing to place."""
    for p in udr.pages:
        for b in p.blocks:
            if b.bbox and b.bbox[2] > b.bbox[0] and b.bbox[3] > b.bbox[1] and b.text:
                return True
    return False


def _page_image(src: str, page_no: int) -> tuple[bytes, float, float] | None:
    """Rasterise one PDF page -> (png/jpeg, width_pt, height_pt)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(src)
    except Exception:
        log.warning("cannot rasterise %s as PDF", src)
        return None
    try:
        if page_no - 1 >= len(pdf):
            return None
        page = pdf[page_no - 1]
        w_pt, h_pt = page.get_width(), page.get_height()
        bitmap = page.render(scale=2.0)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="JPEG", quality=85)
        page.close()
        return buf.getvalue(), w_pt, h_pt
    except Exception:
        log.exception("page raster failed for %s", src)
        return None
    finally:
        pdf.close()


def build_searchable_pdf(src_path: str, udr, original_ext: str) -> tuple[bytes | None, bool]:
    """Returns (pdf_bytes, searchable). pdf_bytes None => caller cannot build
    (missing deps or no coordinates); searchable False => produced without a
    text layer — the artifact row records the reminder either way."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
    except ImportError:
        log.warning("reportlab missing; searchable PDF unavailable")
        return None, False
    for f in ("STSong-Light", "HeiseiMin-W3", "HYSMyeongJo-Medium"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(f))
        except Exception:                     # pragma: no cover - built-ins
            pass
    if not _has_coords(udr):
        return None, False

    buf = io.BytesIO()
    pages = list(udr.pages)
    c = None
    for page in pages:
        if original_ext.lower() == ".pdf":
            img = _page_image(src_path, page.page_no)
            if img is None:
                return None, False
            png, w_pt, h_pt = img
        else:
            # image source: 1px = 1pt keeps the page the size of the picture
            from PIL import Image
            with Image.open(src_path) as im:
                w_pt, h_pt = im.size
            with open(src_path, "rb") as fh:
                png = fh.read()
        if c is None:
            c = canvas.Canvas(buf, pagesize=(w_pt, h_pt))
        else:
            c.setPageSize((w_pt, h_pt))
        # reportlab>=5 wants an ImageReader (filename/PIL/bytes), not raw IO
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(io.BytesIO(png)), 0, 0,
                    width=w_pt, height=h_pt)
        udr_w = float(page.width or w_pt)
        udr_h = float(page.height or h_pt)
        sx = w_pt / udr_w
        sy = h_pt / udr_h
        for b in page.blocks:
            if not b.text or not b.bbox or b.bbox[2] <= b.bbox[0]:
                continue
            x0, y0, x1, y1 = b.bbox
            bw = (x1 - x0) * sx
            bh = (y1 - y0) * sy
            if bw <= 1 or bh <= 1:
                continue
            fs = max(min(bh * 0.8, 12.0), 4.0)
            t = c.beginText()
            t.setFont(_font_for(b.text), fs)
            t.setTextRenderMode(3)          # 3 = invisible text layer
            # top-left UDR y -> bottom-left PDF y
            t.setTextOrigin(x0 * sx, h_pt - y0 * sy - fs)
            t.textOut(b.text)
            c.drawText(t)
        c.showPage()
    if c is None:
        return None, False
    c.save()
    return buf.getvalue(), True
