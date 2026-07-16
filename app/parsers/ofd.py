"""OFD three-level handling (design v0.2 §4.2, context.md M2 item 6). OFD is a
zip of XML (GB/T 33190-2016). Verified against 4 real e-tickets (3 air + 1
rail, 2026-03/06): every国家全电票 carries a structured XBRL attachment.

Level 1 — fast path: Doc_*/Attachs/*.xml XBRL attachment (财政部 taxonomy:
  atr=air transport receipt, rai=railway e-ticket, eit=electronic invoice).
  Structured truth straight from the issuer — no OCR, no LLM ambiguity.
Level 2 — text layer: Pages/Page_*/Content.xml TextObjects carry Boundary
  boxes (mm, origin top-left) — direct UDR blocks with bboxes, same as an
  electronic PDF. Template layers (Tpls) contribute label text too.
Level 3 — render fallback: no attachment + no text = scan wrapped in OFD;
  extract the largest embedded raster and send it down the OCR chain.

All three levels can stack: level 1 fields are prepended to full_markdown so
extraction sees issuer truth first, while level 2 blocks give bbox anchors.
"""
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry

_OFD_NS = "{http://www.ofdspec.org/2016}"

# XBRL fact namespaces of the e-invoice taxonomies (issuer attachments)
_XBRL_FACT_NS = re.compile(r"xbrl\.mof\.gov\.cn/taxonomy")
# noise facts that aren't business fields
_XBRL_SKIP = {"QrCode"}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xbrl_fields(xml_bytes: bytes) -> list[tuple[str, str]]:
    """Flatten an issuer XBRL attachment into (field, value) pairs, tuples
    (e.g. flight segments) included in document order."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    if "xbrl" not in root.tag.lower():
        return []
    fields: list[tuple[str, str]] = []
    for el in root.iter():
        ns = el.tag.rsplit("}", 1)[0] if "}" in el.tag else ""
        if not _XBRL_FACT_NS.search(ns):
            continue
        name = _localname(el.tag)
        text = (el.text or "").strip()
        if text and name not in _XBRL_SKIP:
            fields.append((name, text))
    return fields


def _mm_box(boundary: str) -> list[float] | None:
    """OFD Boundary='x y w h' (mm, top-left origin) -> UDR [x0,y0,x1,y1]."""
    try:
        x, y, w, h = (float(v) for v in boundary.split())
        return [x, y, x + w, y + h]
    except ValueError:
        return None


def _text_blocks(content_xml: bytes) -> tuple[list[Block], float, float]:
    """TextObjects of a page/template Content.xml -> UDR blocks (mm space)."""
    root = ET.fromstring(content_xml)
    width = height = 0.0
    pb = root.find(f".//{_OFD_NS}PhysicalBox")
    if pb is not None and pb.text:
        try:
            _, _, width, height = (float(v) for v in pb.text.split())
        except ValueError:
            pass
    blocks: list[Block] = []
    for tobj in root.iter(f"{_OFD_NS}TextObject"):
        text = " ".join((tc.text or "").strip()
                        for tc in tobj.iter(f"{_OFD_NS}TextCode")).strip()
        if not text:
            continue
        blocks.append(Block(text=text, bbox=_mm_box(tobj.get("Boundary", ""))))
    return blocks, width, height


@registry.register("parser", "ofd")
class OFDParser:
    def parse(self, path: str) -> UDR:
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise ParserUnavailable(f"not a valid OFD (zip) file: {path}") from e
        with zf:
            names = zf.namelist()

            # —— Level 1: structured XBRL attachments (issuer truth) ——
            xbrl_fields: list[tuple[str, str]] = []
            for n in names:
                if "/Attachs/" in n and n.lower().endswith(".xml") \
                        and not n.endswith("Attachments.xml"):
                    xbrl_fields.extend(_xbrl_fields(zf.read(n)))

            # —— Level 2: page text layer (+ template labels), bbox in mm ——
            pages: list[Page] = []
            page_names = sorted(n for n in names
                                if re.search(r"/Pages/Page_\d+/Content\.xml$", n))
            tpl_names = sorted(n for n in names
                               if re.search(r"/Tpls/Tpl_\d+/Content\.xml$", n))
            tpl_blocks: list[Block] = []
            for n in tpl_names:
                b, _, _ = _text_blocks(zf.read(n))
                tpl_blocks.extend(b)
            for i, n in enumerate(page_names):
                blocks, w, h = _text_blocks(zf.read(n))
                # template text (form labels) belongs to every page it decorates;
                # real OFDs bind templates per page — single-template files are
                # the overwhelming e-ticket case, so prepend to page 1 only
                if i == 0 and tpl_blocks:
                    blocks = tpl_blocks + blocks
                pages.append(Page(page_no=i + 1, width=w, height=h,
                                  blocks=blocks,
                                  markdown="\n".join(b.text for b in blocks)))

            has_text = any(p.blocks for p in pages)

            # —— Level 3: neither attachment nor text -> scan wrapped in OFD ——
            if not xbrl_fields and not has_text:
                return self._render_fallback(zf, names, path)

        md_parts: list[str] = []
        if xbrl_fields:
            md_parts.append("## 结构化凭证数据（发行方 XBRL 附件，权威值）")
            md_parts.extend(f"{k}: {v}" for k, v in xbrl_fields)
        md_parts.extend(p.markdown for p in pages if p.markdown)

        if not pages:  # attachment-only OFD still needs a page container
            pages = [Page(page_no=1)]
        return UDR(pages=pages, full_markdown="\n".join(md_parts), parser="ofd",
                   lang=["zh"])

    def _render_fallback(self, zf: zipfile.ZipFile, names: list[str],
                         path: str) -> UDR:
        """Best raster we can get without a full OFD renderer: the largest
        embedded image (scan-wrapped OFDs embed the page as one big picture),
        sent down the configured OCR degradation chain."""
        import tempfile

        from app.parsers.router import _scan_parse
        images = [n for n in names
                  if n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))]
        if not images:
            raise ParserUnavailable(
                f"OFD has no XBRL attachment, no text layer and no raster: {path}")
        biggest = max(images, key=lambda n: zf.getinfo(n).file_size)
        suffix = Path(biggest).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(zf.read(biggest))
            tmp_path = tmp.name
        try:
            udr = _scan_parse(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        udr.parser = f"ofd+{udr.parser}"      # quality gates see the degradation
        return udr
