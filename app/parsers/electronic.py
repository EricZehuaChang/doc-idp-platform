"""Electronic-document parsers (design v0.2 §4.2): text layer is extracted
directly — never run OCR/models on born-digital files (zero cost, zero OCR error).

- pdfplumber (MIT): electronic PDF, words carry bbox -> verification highlight.
  PyMuPDF is BANNED (AGPL red line, feasibility v1.0 §4).
- markitdown (MIT): Office family -> Markdown; no bbox by nature (acceptable).
Both are optional extras: imported lazily so the core app runs without them.
"""
from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry


def _char_boxes(text: str, chars: list[dict]) -> list[list[float] | None] | None:
    """Align pdfplumber char objects 1:1 with the assembled line text.
    Spaces the assembler inserted between words have no glyph -> None entry.
    Any desync (ligatures, stripped glyphs) bails to None: a wrong per-char
    map would produce wrong redaction boxes, the whole-line bbox never does."""
    boxes: list[list[float] | None] = []
    j = 0
    for ch in text:
        if j < len(chars) and chars[j].get("text") == ch:
            c = chars[j]
            boxes.append([float(c["x0"]), float(c["top"]),
                          float(c["x1"]), float(c["bottom"])])
            j += 1
        elif ch.isspace():
            boxes.append(None)
        else:
            return None
    # trailing unconsumed glyphs mean the text was stripped: mapping unsafe
    return boxes if j == len(chars) else None


@registry.register("parser", "pdfplumber")
class PdfPlumberParser:
    def parse(self, path: str) -> UDR:
        try:
            import pdfplumber
        except ImportError as e:
            raise ParserUnavailable("pdfplumber not installed (pip install .[parsers])") from e
        pages: list[Page] = []
        md_parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for i, p in enumerate(pdf.pages):
                # line-level blocks: group words by line for usable bbox granularity
                blocks: list[Block] = []
                for line in (p.extract_text_lines() or []):
                    text = line.get("text", "")
                    blocks.append(Block(
                        text=text,
                        bbox=[line.get("x0", 0), line.get("top", 0),
                              line.get("x1", 0), line.get("bottom", 0)],
                        chars=_char_boxes(text, line.get("chars") or [])))
                text = p.extract_text() or ""
                md_parts.append(text)
                pages.append(Page(page_no=i + 1, width=float(p.width),
                                  height=float(p.height), blocks=blocks))
        if not any(pg.blocks for pg in pages):
            # no text layer -> this is a scanned PDF, route to OCR instead
            raise ParserUnavailable("PDF has no text layer; route to scan parser")
        return UDR(pages=pages, full_markdown="\n\n".join(md_parts), parser="pdfplumber")


@registry.register("parser", "markitdown")
class MarkitdownParser:
    def parse(self, path: str) -> UDR:
        try:
            from markitdown import MarkItDown
        except ImportError as e:
            raise ParserUnavailable("markitdown not installed (pip install .[parsers])") from e
        md = MarkItDown().convert(path).text_content or ""
        page = Page(page_no=1, blocks=[Block(text=md)])
        return UDR(pages=[page], full_markdown=md, parser="markitdown")
