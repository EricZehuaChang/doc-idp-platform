"""OFD three-level parser tests (design v0.2 §4.2, context.md M2 item 6).

Synthetic OFD zips exercise all three levels deterministically; the real
e-ticket suite (3 air + 1 rail, sensitive → gitignored samples/) runs when
present and proves the XBRL fast path + mm text layer on issuer-grade files.
"""
import zipfile
from pathlib import Path

import pytest

from app.parsers.ofd import OFDParser
from app.parsers.base import ParserUnavailable

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "ofd"

XBRL = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:rai="http://xbrl.mof.gov.cn/taxonomy/2022-01-19/rai">
  <xbrli:context id="C1"><xbrli:entity>
    <xbrli:identifier scheme="http://xbrl.mof.gov.cn">测试发行方</xbrli:identifier>
  </xbrli:entity></xbrli:context>
  <rai:TrainNumber contextRef="C1">G99</rai:TrainNumber>
  <rai:Fare contextRef="C1">123.45</rai:Fare>
  <rai:QrCode contextRef="C1">noise,should,be,skipped</rai:QrCode>
</xbrli:xbrl>"""

CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Area><ofd:PhysicalBox>0 0 210 140</ofd:PhysicalBox></ofd:Area>
  <ofd:Content><ofd:Layer ID="1">
    <ofd:TextObject ID="2" Boundary="10 20 50 6" Font="80" Size="4.2">
      <ofd:TextCode X="0" Y="4.2">发票号码:123456</ofd:TextCode>
    </ofd:TextObject>
  </ofd:Layer></ofd:Content>
</ofd:Page>"""


def _make_ofd(path: Path, *, attach: bool, text: bool, image: bool = False) -> str:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        if attach:
            z.writestr("Doc_0/Attachs/rai_issuer_test.xml", XBRL)
        if text:
            z.writestr("Doc_0/Pages/Page_0/Content.xml", CONTENT)
        if image:
            z.writestr("Doc_0/Res/scan.png", b"\x89PNG fake image bytes")
    return str(path)


def test_level1_xbrl_fast_path(tmp_path):
    udr = OFDParser().parse(_make_ofd(tmp_path / "a.ofd", attach=True, text=False))
    assert udr.parser == "ofd"
    assert "TrainNumber: G99" in udr.full_markdown
    assert "Fare: 123.45" in udr.full_markdown
    assert "QrCode" not in udr.full_markdown        # noise fact skipped
    assert "权威值" in udr.full_markdown             # issuer-truth header present


def test_level2_text_layer_mm_bbox(tmp_path):
    udr = OFDParser().parse(_make_ofd(tmp_path / "b.ofd", attach=False, text=True))
    page = udr.pages[0]
    assert (page.width, page.height) == (210, 140)  # PhysicalBox mm
    blk = page.blocks[0]
    assert blk.text == "发票号码:123456"
    assert blk.bbox == [10, 20, 60, 26]             # x y w h -> x0 y0 x1 y1
    assert "发票号码:123456" in udr.full_markdown


def test_levels_stack_attachment_plus_text(tmp_path):
    udr = OFDParser().parse(_make_ofd(tmp_path / "c.ofd", attach=True, text=True))
    md = udr.full_markdown
    # issuer truth first, page text after (extraction sees both)
    assert md.index("TrainNumber: G99") < md.index("发票号码:123456")
    assert udr.pages[0].blocks                       # bbox anchors intact


def test_level3_raster_fallback_routes_to_ocr(tmp_path, monkeypatch):
    from app.parsers.base import UDR, Page
    seen = {}

    def fake_scan(p):
        seen["path"] = p
        return UDR(pages=[Page(page_no=1)], full_markdown="ocr text", parser="rapidocr")

    import app.parsers.router as router_mod
    monkeypatch.setattr(router_mod, "_scan_parse", fake_scan)
    udr = OFDParser().parse(_make_ofd(tmp_path / "d.ofd", attach=False, text=False,
                                      image=True))
    assert udr.parser == "ofd+rapidocr"              # degradation is visible
    assert seen["path"].endswith(".png")             # largest raster extracted


def test_level3_nothing_at_all_raises(tmp_path):
    with pytest.raises(ParserUnavailable):
        OFDParser().parse(_make_ofd(tmp_path / "e.ofd", attach=False, text=False))


def test_not_a_zip_raises(tmp_path):
    bad = tmp_path / "f.ofd"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ParserUnavailable):
        OFDParser().parse(str(bad))


@pytest.mark.skipif(not SAMPLES.exists(), reason="real OFD samples not on this machine")
def test_real_etickets_fast_path():
    """3 air (atr) + 1 rail (rai) real e-tickets: every one must hit level 1
    (XBRL) AND level 2 (text layer with mm geometry). Amount facts must appear
    with the exact values encoded in the file names (490/517/650/520)."""
    files = sorted(SAMPLES.glob("*.ofd"))
    assert len(files) >= 4
    for f in files:
        udr = OFDParser().parse(str(f))
        md = udr.full_markdown
        assert udr.parser == "ofd"
        assert "权威值" in md, f.name                # level 1 hit
        assert udr.pages[0].width > 0 and udr.pages[0].blocks, f.name  # level 2 hit
        if "高铁" in f.name:
            assert "TrainNumber" in md and "Fare: 517.00" in md
        else:
            assert "Flight" in md and "TotalAmount" in md
        # amount from the file name must be locatable (consistency channel food)
        import re
        amount = re.search(r"(\d+)$", f.stem).group(1)
        assert f"{amount}.00" in md, f.name
