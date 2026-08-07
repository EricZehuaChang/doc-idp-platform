"""POST /api/v1/detect — seal/signature visual detection channel (the visual
twin of /locate). Covers: pp_doclayout post-processing (threshold/class-map/
clamping), the red-seal CV detector on synthetic images (real end-to-end, no
model weights needed), percent + bbox_px dual coordinates, kinds filtering
and misses, validation 422s, DetectorUnavailable 422 (missing weights),
auth-on 401, and the pure-compute guarantee (no Transaction/FileRecord/
ledger rows). Real-ONNX canary is opt-in: it runs iff the 261MB weights sit
in data/models/ (configs/detectors.yaml model_source).
"""
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.routes.detect as detect_route
from app.config import DetectorCfg
from app.detectors.base import DetectorUnavailable, PageImage, Region
from app.detectors.pp_doclayout import _rows_to_regions
from app.plugins.registry import registry

# ---------------- pp_doclayout post-processing (pure, no onnxruntime) -------


def test_rows_to_regions_filters_and_clamps():
    page = PageImage(page_no=2, image=None, width=1000.0, height=500.0)
    rows = [
        [20, 0.90, 10, 20, 110, 120, 0],       # seal, kept
        [20, 0.30, 10, 20, 110, 120, 1],       # below threshold
        [5, 0.99, 10, 20, 110, 120, 2],        # class not in map
        [20, 0.80, -50, -10, 2000, 600, 3],    # clamps to page bounds
        [20, 0.90, 100, 100, 100.5, 300, 4],   # degenerate after clamp
    ]
    regions = _rows_to_regions(rows, {20: "seal"}, 0.5, page)
    assert [r.label for r in regions] == ["seal", "seal"]
    assert regions[0].bbox == [10.0, 20.0, 110.0, 120.0]
    assert regions[0].page == 2 and regions[0].score == 0.9
    assert regions[1].bbox == [0.0, 0.0, 1000.0, 500.0]


# ---------------- red-seal CV detector on synthetic pages -------------------


def _stamp_page(size=(800, 600)):
    """White page with a red seal-like ring plus a thin red rule (the rule
    must be rejected by the aspect filter, the ring must survive)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.ellipse([300, 200, 460, 360], outline=(200, 30, 30), width=12)
    d.line([(0, 500), (size[0], 500)], fill=(200, 30, 30), width=3)
    return img


def test_red_seal_cv_finds_ring_rejects_rule():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from app.detectors.red_seal_cv import RedSealCvDetector
    img = _stamp_page()
    pages = [PageImage(page_no=1, image=img, width=800.0, height=600.0)]
    regions = RedSealCvDetector().detect(pages)
    assert len(regions) == 1, [r.bbox for r in regions]
    r = regions[0]
    assert r.label == "seal" and r.page == 1 and 0 < r.score <= 0.95
    # covers the ring (redaction channel: recall over tightness)
    x0, y0, x1, y1 = r.bbox
    assert x0 <= 302 and y0 <= 202 and x1 >= 458 and y1 >= 358
    assert x1 - x0 < 300 and y1 - y0 < 300   # ...but not half the page


def test_red_seal_cv_rejects_letterhead_glyph_run():
    """A red-header text line (glyph blocks with small gaps, e.g. 太和县…局)
    must NOT come back as seals: the glue-merge turns the run into one wide
    strip and the aspect filter rejects it."""
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image, ImageDraw
    from app.detectors.red_seal_cv import RedSealCvDetector
    img = Image.new("RGB", (800, 600), "white")
    d = ImageDraw.Draw(img)
    for i in range(8):                       # 8 glyph-sized blocks, 4px gaps
        x = 100 + i * 44
        d.rectangle([x, 100, x + 40, 140], fill=(200, 30, 30))
    pages = [PageImage(page_no=1, image=img, width=800.0, height=600.0)]
    assert RedSealCvDetector().detect(pages) == []


def test_red_seal_cv_blank_page_no_regions():
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    from PIL import Image
    from app.detectors.red_seal_cv import RedSealCvDetector
    img = Image.new("RGB", (400, 300), "white")
    pages = [PageImage(page_no=1, image=img, width=400.0, height=300.0)]
    assert RedSealCvDetector().detect(pages) == []


# ---------------- API level (auth off: conftest default) --------------------


@registry.register("detector", "fake-fixed")
class _FakeFixed:
    """Deterministic detector: exercises conversion/sort/filter, no model.
    Registered under a name that only exists in the monkeypatched config —
    the yaml whitelist keeps it unreachable for every other test."""

    def detect(self, pages):
        return [
            Region(page=1, label="signature", bbox=[100, 40, 150, 90], score=0.8),
            Region(page=1, label="seal", bbox=[10, 20, 60, 80], score=0.9),
        ]


@registry.register("detector", "fake-broken")
class _FakeBroken:
    def detect(self, pages):
        raise DetectorUnavailable("weights gone fishing")


def _fake_cfgs():
    return {"detectors": {n: DetectorCfg(name=n, type="cv")
                          for n in ("fake-fixed", "fake-broken", "red-seal-cv")},
            # all tiers mapped: a leaked IDP_DEPLOY_TIER must not skew routing
            "default_detector": {t: "fake-fixed"
                                 for t in ("lite", "standard", "air_gapped")}}


def _png_bytes(width=200, height=100, draw_stamp=False) -> bytes:
    from PIL import Image
    img = _stamp_page((width, height)) if draw_stamp \
        else Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _blank_pdf_bytes(n_pages: int = 1, box: str = "0 0 612 792") -> bytes:
    """Minimal n-page blank PDF — pypdfium2 renders white pages. `box` is the
    MediaBox, so tests can declare pathological page geometry in a tiny file."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode()]
    for _ in range(n_pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [{box}] >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Isolated app + DB per test (same insulation as test_locate_api)."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            yield c


async def test_detect_dual_coordinates_sort_and_misses(client, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("page.png", _png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count"] == 1 and body["detector"] == "fake-fixed"
    assert body["pages_scanned"] == 1 and body["truncated"] is False
    assert body["pages"] == [{"page": 1, "width": 200.0, "height": 100.0}]
    assert body["misses"] == []
    seal, sig = body["regions"]                # sorted by (page, y, x)
    assert seal["label"] == "seal" and sig["label"] == "signature"
    # percent contract (mask-guard downstream): [10,20,60,80] on 200x100
    assert (seal["x"], seal["y"], seal["w"], seal["h"]) == (5.0, 20.0, 25.0, 60.0)
    # pixel contract (DocStage overlay downstream)
    assert seal["bbox_px"] == [10, 20, 60, 80] and seal["score"] == 0.9
    for e in (seal, sig):
        assert 0 <= e["x"] and e["x"] + e["w"] <= 100
        assert 0 <= e["y"] and e["y"] + e["h"] <= 100


async def test_detect_kinds_filter_and_misses(client, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("page.png", _png_bytes(), "image/png")},
        data={"kinds": "signature"})
    assert r.status_code == 200
    body = r.json()
    assert [e["label"] for e in body["regions"]] == ["signature"]
    assert body["misses"] == []                # signature requested AND found

    r = await client.post(                     # red-seal-cv on a blank page:
        "/api/v1/detect",                      # both kinds requested, none found
        files={"file": ("page.png", _png_bytes(), "image/png")},
        data={"detector": "red-seal-cv"})
    assert r.status_code == 200
    assert r.json()["regions"] == []
    assert r.json()["misses"] == ["seal", "signature"]


async def test_detect_red_seal_cv_end_to_end_png(client, monkeypatch):
    """Full pipeline with zero mocks: PNG upload -> Pillow -> HSV CV -> boxes."""
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("stamp.png", _png_bytes(800, 600, draw_stamp=True),
                        "image/png")},
        data={"detector": "red-seal-cv", "kinds": "seal"})
    assert r.status_code == 200, r.text
    regions = r.json()["regions"]
    assert len(regions) == 1
    px = regions[0]["bbox_px"]
    assert px[0] <= 302 and px[2] >= 458      # box covers the drawn ring
    assert regions[0]["x"] + regions[0]["w"] <= 100


async def test_detect_pdf_rasterization_dims(client, monkeypatch):
    """PDF path: pypdfium2 renders 612x792pt at 200 DPI -> 1700x2200 px, and
    those dims are BOTH the percent base and the pages[] payload."""
    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("doc.pdf", _blank_pdf_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count"] == 1
    page = body["pages"][0]
    assert round(page["width"]) == 1700 and round(page["height"]) == 2200
    seal = next(e for e in body["regions"] if e["label"] == "seal")
    assert seal["x"] == round(10 / page["width"] * 100, 2)


async def test_detect_truncation_is_explicit(client, monkeypatch):
    """Documents past the page cap must be reported as truncated, never as an
    authoritative "no seal found" over pages that were never scanned."""
    pytest.importorskip("pypdfium2")
    from app.detectors import render as render_module
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    monkeypatch.setattr(render_module, "_MAX_PAGES", 2)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("long.pdf", _blank_pdf_bytes(n_pages=3), "application/pdf")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count"] == 3           # the document's true page count
    assert body["pages_scanned"] == 2 and body["truncated"] is True
    assert len(body["pages"]) == 2


async def test_detect_raster_bomb_page_downscaled(client, monkeypatch):
    """A tiny PDF declaring a 14400x14400pt MediaBox (PDF spec max) must NOT
    raster at 200 DPI (=40000^2 px, ~4.8GB RGB): the per-page pixel cap
    renders it at reduced DPI instead."""
    pytest.importorskip("pypdfium2")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("bomb.pdf", _blank_pdf_bytes(box="0 0 14400 14400"),
                        "application/pdf")})
    assert r.status_code == 200, r.text
    page = r.json()["pages"][0]
    # pdfium rounds output dims up, allow sub-permille slack over the cap
    assert page["width"] * page["height"] <= 24_000_000 * 1.001


async def test_detect_raster_budget_422(client, monkeypatch):
    """Per-document pixel budget exceeded -> explicit 422, never an OOM/500."""
    pytest.importorskip("pypdfium2")
    from app.detectors import render as render_module
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    monkeypatch.setattr(render_module, "_MAX_TOTAL_PIXELS", 1_000_000)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("big.pdf", _blank_pdf_bytes(n_pages=2), "application/pdf")})
    assert r.status_code == 422
    assert "栅格总量超限" in r.json()["detail"]


async def test_detect_extreme_aspect_image_no_500(client, monkeypatch):
    """A 3000x1 image must not crash the work-image resize (0-height)."""
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("ribbon.png", _png_bytes(3000, 1), "image/png")},
        data={"detector": "red-seal-cv"})
    assert r.status_code == 200, r.text
    assert r.json()["regions"] == []


async def test_detect_validation_422s(client, monkeypatch):
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    png = ("page.png", b"\x89PNG fake", "image/png")
    r = await client.post("/api/v1/detect", files={"file": png},
                          data={"kinds": "seal,face"})
    assert r.status_code == 422 and "kinds" in r.json()["detail"]
    r = await client.post("/api/v1/detect", files={"file": png},
                          data={"detector": "nope"})
    assert r.status_code == 422 and "detector" in r.json()["detail"]
    r = await client.post("/api/v1/detect",
                          files={"file": ("a.docx", b"PK", "application/zip")})
    assert r.status_code == 422
    assert "不支持的文件类型" in r.json()["detail"]


async def test_detect_detector_unavailable_422(client, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("page.png", _png_bytes(), "image/png")},
        data={"detector": "fake-broken"})
    assert r.status_code == 422
    assert "检测不可用" in r.json()["detail"]


async def test_detect_missing_weights_422_names_path(client, tmp_path, monkeypatch):
    """Real pp-doclayout wiring (yaml config, no mocks): absent weights must
    yield an explicit 422 naming the path + source, never a silent []. The
    env override pins the path so a developer's downloaded model can't flip
    this test into real inference."""
    pytest.importorskip("onnxruntime")
    monkeypatch.setenv("IDP_SEAL_MODEL_PATH", str(tmp_path / "absent.onnx"))
    import app.config as config
    config.get_settings.cache_clear()
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("page.png", _png_bytes(), "image/png")},
        data={"detector": "pp-doclayout"})
    assert r.status_code == 422, r.text
    assert "模型权重缺失" in r.json()["detail"]
    assert "absent.onnx" in r.json()["detail"]


async def test_detect_writes_no_rows(client, tmp_path, monkeypatch):
    """Pure-compute contract, same as /locate: no Transaction/FileRecord,
    no billing entries, nothing persisted under data_dir/files."""
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    monkeypatch.setattr(detect_route, "load_detectors", _fake_cfgs)
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("page.png", _png_bytes(), "image/png")},
        data={"detector": "red-seal-cv"})
    assert r.status_code == 200
    from sqlalchemy import func, select
    from app.db import session_factory
    from app.models import CreditLedger, FileRecord, Transaction
    async with session_factory()() as s:
        for model in (Transaction, FileRecord, CreditLedger):
            n = (await s.execute(select(func.count()).select_from(model))).scalar_one()
            assert n == 0, model.__name__
    assert not (tmp_path / "files").exists()


# ---------------- auth surface (same middleware as /process & /locate) ------


async def test_detect_requires_credential_when_auth_on(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    pytest.importorskip("numpy")
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IDP_AUTH_MODE", "on")
    monkeypatch.setenv("IDP_ADMIN_PASSWORD", "admin-pass-123")
    import app.config as config
    import app.db as db
    from app.auth import security
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    security._secret = None
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as c:
            body = {"files": {"file": ("p.png", _png_bytes(), "image/png")},
                    "data": {"detector": "red-seal-cv"}}
            r = await c.post("/api/v1/detect", **body)
            assert r.status_code == 401       # no ticket
            r = await c.post("/api/v1/detect", **body,
                             headers={"Authorization": "Bearer garbage"})
            assert r.status_code == 401       # bogus ticket
            r = await c.post("/api/v1/auth/login",
                             json={"email": "admin@example.com",
                                   "password": "admin-pass-123"})
            token = r.json()["access_token"]
            r = await c.post("/api/v1/detect", **body,
                             headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200 and r.json()["regions"] == []


# ---------------- opt-in canary: real ONNX weights --------------------------

_MODEL = Path(__file__).resolve().parents[1] / "data" / "models" / "PP-DocLayoutV3.onnx"


@pytest.mark.skipif(not _MODEL.exists(),
                    reason="PP-DocLayoutV3.onnx not downloaded (opt-in canary; "
                           "see configs/detectors.yaml model_source)")
async def test_canary_pp_doclayout_real_inference(client):
    """Contract-level canary on the real export: the request must complete and
    every region must satisfy both coordinate contracts. Class-map recall on
    actual KMBP stamp samples is phase 3 (design §5) — not asserted here."""
    pytest.importorskip("onnxruntime")
    r = await client.post(
        "/api/v1/detect",
        files={"file": ("stamp.png", _png_bytes(800, 600, draw_stamp=True),
                        "image/png")},
        data={"detector": "pp-doclayout"})
    assert r.status_code == 200, r.text
    for e in r.json()["regions"]:
        assert e["label"] in {"seal", "signature"}
        assert 0 <= e["x"] and e["x"] + e["w"] <= 100
        assert len(e["bbox_px"]) == 4
