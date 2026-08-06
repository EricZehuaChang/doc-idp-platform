"""opendataloader-pdf parser (Apache-2.0, veraPDF-lineage Java engine): the
first-pass parser for electronic PDFs (design §4.2 extension, license red line
respected — Apache-2.0 may ship in closed-source deliverables, unlike the
banned AGPL family).

Why it leads the electronic chain: deterministic CPU layout analysis emits
headings/reading order/bordered tables with an element-level bbox — including
per-table-cell anchors — which is strictly finer than pdfplumber's line
grouping, at zero token cost and sub-second per page (measured vs the
200-290s/doc cloud OCR path on large digital PDFs).

Contracts kept identical to pdfplumber so the router chain stays honest:
- package missing / no Java runtime / JVM failure -> ParserUnavailable
  (router falls back to pdfplumber; behavior = pre-upgrade)
- no text layer (scanned PDF) -> ParserUnavailable -> OCR degradation chain
Known limit (measured on Huaqin SGS/CTI samples): borderless tables come out
as reading-order paragraphs, not table blocks — the runner's table-escalation
rule (router.escalate_if_tables_missing) re-parses with the scan-tier engine
when a skill expects table fields.
"""
import re
import shutil
import tempfile
from pathlib import Path

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

# PDF text layers store text line-by-line; joining lines injects spaces between
# CJK characters ("监 管平台"). Strip only CJK-to-CJK horizontal gaps: newlines
# carry Markdown structure and Latin word spacing is legitimate content. This
# protects verbatim-field consistency checks from false mismatches.
_CJK = r"[㐀-䶿一-鿿豈-﫿　-〿！-｠]"
_CJK_GAP = re.compile(f"(?<={_CJK})[ \t]+(?={_CJK})")


def strip_cjk_gaps(text: str) -> str:
    return _CJK_GAP.sub("", text)


def flip_bbox(bbox: list, page_height: float) -> list[float]:
    """opendataloader bbox is [left, bottom, right, top] in PDF points with a
    bottom-left origin; UDR uses [x0, top, x1, bottom] top-left origin (same
    units — page.width/height are also PDF points here, matching pdfplumber)."""
    left, bottom, right, top = (float(v) for v in bbox)
    return [left, page_height - top, right, page_height - bottom]


@registry.register("parser", "opendataloader")
class OpenDataLoaderParser:
    def parse(self, path: str) -> UDR:
        try:
            import opendataloader_pdf
        except ImportError as e:
            raise ParserUnavailable(
                "opendataloader-pdf not installed (pip install .[parsers])") from e
        if shutil.which("java") is None:
            raise ParserUnavailable("java runtime (11+) not found for opendataloader")
        import json

        from pypdf import PdfReader

        # Page dimensions come from pypdf: the opendataloader JSON carries
        # per-element bboxes but no page media boxes, and the verification
        # UI needs width/height to scale SVG highlights.
        try:
            sizes = [(float(p.mediabox.width), float(p.mediabox.height))
                     for p in PdfReader(path).pages]
        except Exception as e:
            raise ParserUnavailable(f"unreadable PDF: {e}") from e

        with tempfile.TemporaryDirectory() as tmp:
            try:
                # image_output="off": figures are not extracted — extraction
                # works on text; keeps temp I/O minimal and deterministic.
                # include_header_footer: ODL drops repeating page headers and
                # footers by default (they are chrome for a reading-order
                # product).  Here they are content — a page-footer URL or a
                # letterhead is exactly the kind of value a masking or
                # extraction skill must see, and losing it is silent.
                opendataloader_pdf.convert(
                    input_path=str(path), output_dir=tmp,
                    format="json,markdown", image_output="off", quiet=True,
                    include_header_footer=True)
            except Exception as e:
                raise ParserUnavailable(f"opendataloader failed: {e}") from e
            files = list(Path(tmp).iterdir())
            js = next((f for f in files if f.suffix == ".json"), None)
            md = next((f for f in files if f.suffix == ".md"), None)
            if js is None:
                raise ParserUnavailable("opendataloader produced no JSON output")
            doc = json.loads(js.read_text(encoding="utf-8"))
            markdown = strip_cjk_gaps(md.read_text(encoding="utf-8")) if md else ""

        pages = [Page(page_no=i + 1, width=w, height=h)
                 for i, (w, h) in enumerate(sizes)]
        self._walk(doc.get("kids", []), pages, in_table=False)
        if not any(b.text.strip() for p in pages for b in p.blocks):
            # Same sentence pdfplumber uses — the router keys the OCR
            # fallback on ParserUnavailable, not on the message, but keeping
            # the wording aligned makes logs comparable across the chain.
            raise ParserUnavailable("PDF has no text layer; route to scan parser")
        return UDR(pages=pages, full_markdown=markdown, parser="opendataloader")

    def _walk(self, nodes, pages: list[Page], in_table: bool) -> None:
        """Flatten the element tree into per-page UDR blocks.

        Children hang off several keys depending on the element: "kids" for
        containers, "rows"/"cells" for tables, "list items" for lists.  Rather
        than enumerate them (an omission is a silent content loss — lists cost
        us every numbered clause until this was found), descend EVERY list-of-
        objects value; scalar arrays like "bounding box" are skipped by the
        dict check.  Blocks inside a table keep type="table" so the escalation
        rule and cell-level highlight anchors can see table coverage."""
        for node in nodes:
            if not isinstance(node, dict):
                continue
            ntype = node.get("type", "")
            content = node.get("content")
            page_no = node.get("page number")
            bbox = node.get("bounding box")
            if content and isinstance(page_no, int) and 1 <= page_no <= len(pages):
                page = pages[page_no - 1]
                if in_table:
                    btype = "table"
                elif ntype == "heading":
                    btype = "title"
                else:
                    btype = "text"
                page.blocks.append(Block(
                    type=btype, text=strip_cjk_gaps(str(content)),
                    bbox=flip_bbox(bbox, page.height) if bbox else None))
            now_in_table = in_table or ntype in ("table", "table cell")
            for sub in node.values():
                if isinstance(sub, list) and any(isinstance(x, dict) for x in sub):
                    self._walk(sub, pages, now_in_table)
