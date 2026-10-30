"""Layout pass-through view of a parsed document (kind="layout" skills).

The "原文 + 坐标" product: the parser's own output (UDR) handed to the API
caller as-is — every region's text and box in the parser's page space plus
the page dimensions, so the caller can normalize, highlight or crop on their
own. No model touches these numbers: GLM-OCR regions come straight from
/layout_parsing, electronic PDFs from the text layer.

Coordinate contract (same space as a field's `$bbox` on the same file):
- bbox = [x0, y0, x1, y1], top-left origin, in the page's width/height space;
  the unit depends on the parser (pdfplumber = PDF points, OCR = render
  pixels), which is exactly why the page dims travel with it; for regions
  with glyph geometry it spans only the glyphs that sit on the page;
- `chars` (one box per character of `text`, null for synthetic characters)
  only appears when the parser has glyph geometry (pdfplumber);
- bbox is null when the parser has no geometry (markitdown Office files).
"""
from app.parsers.base import UDR


def _round_box(box: list[float] | None) -> list[float] | None:
    # 2 decimals: sub-pixel noise only inflates the payload
    return [round(float(v), 2) for v in box] if box else None


def _visible_bbox(chars: list, width: float, height: float) -> list[float] | None:
    """Union of the glyphs that sit on the page. Text-layer watermarks (e.g.
    a recruiting site's anti-leak string) park glyphs off-page, and the
    parser's line box stretches to include them — a resume line came back
    starting at x=-35 instead of 24. None when no glyph is on the page."""
    boxes = [c for c in chars if c and c[2] > 0 and c[0] < width
             and c[3] > 0 and c[1] < height]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def layout_view(udr: UDR) -> dict:
    """UDR -> {parser, pages: [{page, width, height}], regions: [...]}.
    Regions are flat and in parser reading order, each tagged with its page;
    `index` restarts at 1 on every page. Regions whose glyphs all sit off the
    page are skipped — nobody can see them, so nobody can highlight them."""
    pages = []
    regions = []
    for page in udr.pages:
        pages.append({"page": page.page_no,
                      "width": round(float(page.width or 0), 2),
                      "height": round(float(page.height or 0), 2)})
        index = 0
        for block in page.blocks:
            bbox = block.bbox
            if block.chars and page.width and page.height:
                bbox = _visible_bbox(block.chars, page.width, page.height)
                if bbox is None:
                    # every glyph is off-page: invisible watermark residue
                    # (72 of 316 regions on a recruiting-site resume)
                    continue
            index += 1
            region = {"page": page.page_no, "index": index,
                      "type": block.type, "text": block.text or "",
                      "bbox": _round_box(bbox),
                      "confidence": block.confidence}
            if block.chars:
                region["chars"] = [_round_box(c) for c in block.chars]
            regions.append(region)
    return {"parser": udr.parser, "pages": pages, "regions": regions}
