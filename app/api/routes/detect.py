"""Visual region-detection API (seal/signature redaction channel):
POST /api/v1/detect — locate stamps/signatures on a document for masking.

The visual twin of /locate on the same /api/v1 surface, so it inherits the
exact same auth (TenantMiddleware: Bearer ApiKey or session JWT — external
integrations call it with an idp_ak_ key). Channel rules, mirroring /locate:
- fixed vision model (detector plugin), NOT a skill: no LLM call, no text
  output, no OCR charge — pure compute, so no Transaction/FileRecord/ledger
  rows; the uploaded file lives in a temp dir and is discarded;
- PDF pages rasterize via pypdfium2 (PyMuPDF is BANNED: AGPL);
- DetectorUnavailable (weights/runtime missing, unrenderable file) is an
  explicit 422, never a silent empty result.

Dual coordinate contract (same as /process $bbox vs $hits, design §2.3):
- x/y/w/h    : 0-100 percent, top-left origin -> mask-guard/KMBP redaction
- bbox_px    : [x0,y0,x1,y1] page pixels + pages[] dims -> DocStage overlay
"""
import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings, load_detectors
from app.detectors import (  # noqa: F401  register plugins
    pp_doclayout, red_seal_cv, yolos_signature)
from app.detectors import render
from app.detectors.base import Detector, DetectorUnavailable
from app.plugins.registry import registry

router = APIRouter(prefix="/api/v1", tags=["detect"])

_MAX_SIZE = 50 * 1024 * 1024        # same ceiling as /process and /locate
_KINDS = {"seal", "signature"}


def _parse_kinds(raw: str) -> set[str]:
    """Validate the `kinds` form field: comma-separated subset of seal|signature."""
    kinds = {k.strip() for k in raw.split(",") if k.strip()}
    if not kinds or not kinds <= _KINDS:
        raise HTTPException(
            422, f"kinds 只支持 {','.join(sorted(_KINDS))} 的组合,收到: {raw!r}")
    return kinds


def _make_detector(name: str) -> Detector:
    """Instantiate a configured detector. The detectors.yaml whitelist is the
    gate — an arbitrary registered plugin name is NOT accepted from the wire."""
    cfg = load_detectors()["detectors"].get(name)
    if cfg is None:
        known = ", ".join(load_detectors()["detectors"])
        raise HTTPException(422, f"未知 detector {name!r},可用: {known}")
    kwargs = {}
    if cfg.type == "onnx":
        s = get_settings()
        # env overrides beat yaml (per-host model location, §2.5)
        env_path, env_source = {
            "pp-doclayout": (s.seal_model_path, s.seal_model_source),
            "yolos-signature": (s.signature_model_path, s.signature_model_source),
        }.get(name, ("", ""))
        kwargs = {
            "model_path": env_path or cfg.model_path,
            "model_source": env_source or cfg.model_source or "",
            "score_threshold": cfg.score_threshold,
            "class_map": cfg.class_map,
        }
    return registry.create("detector", name, **kwargs)


def _route_detectors(kind_set: set[str], override: str) -> list[str]:
    """Pick the detector set for a request. Explicit override runs exactly
    that one detector; otherwise every tier-default detector whose declared
    labels intersect the requested kinds runs (seal and signature are
    separate models — one call covers both, replacing the Insavlo pattern
    of a single engine returning both region types)."""
    if override:
        return [override]
    cfg = load_detectors()
    tier = get_settings().deploy_tier
    names = [n for n in cfg["default_detectors"].get(tier, [])
             if n in cfg["detectors"]
             and set(cfg["detectors"][n].labels) & kind_set]
    if not names:
        raise HTTPException(
            422, f"当前部署档({tier})未配置覆盖 kinds={','.join(sorted(kind_set))} 的检测器")
    return names


def _suppress_seal_echoes(regions):
    """Chop faces read as handwriting to the signature model: a signature
    region mostly covered by seal regions on the same page is the seal
    itself, not a signature (measured on the triple-chop sample: every such
    echo overlaps the chops 51-85%). The area is masked via the seal either
    way, so dropping the echo loses nothing for the redaction downstream."""
    seals = [r for r in regions if r.label == "seal"]
    if not seals:
        return regions
    out = []
    for r in regions:
        if r.label == "signature":
            x0, y0, x1, y1 = r.bbox
            area = max((x1 - x0) * (y1 - y0), 1e-6)
            inter = 0.0
            for s in (s for s in seals if s.page == r.page):
                ox = min(x1, s.bbox[2]) - max(x0, s.bbox[0])
                oy = min(y1, s.bbox[3]) - max(y0, s.bbox[1])
                if ox > 0 and oy > 0:
                    inter += ox * oy
            if min(inter, area) / area >= 0.5:
                continue
        out.append(r)
    return out


@router.post("/detect")
async def detect_endpoint(file: UploadFile = File(...),
                          kinds: str = Form("seal,signature"),
                          detector: str = Form("")):
    kind_set = _parse_kinds(kinds)
    det_names = _route_detectors(kind_set, detector.strip())
    dets = [(name, _make_detector(name)) for name in det_names]

    blob = await file.read()
    if len(blob) > _MAX_SIZE:
        raise HTTPException(413, f"file too large: {file.filename}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".pdf" and suffix not in render.IMAGE_SUFFIXES:
        raise HTTPException(
            422, f"不支持的文件类型 {suffix or '(无扩展名)'}:"
                 "印章检测通道仅支持 PDF 与图片(png/jpg/bmp/webp)")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(blob)
        try:
            # thread offload: rasterize + inference are CPU-bound, same
            # discipline as /locate's parse offload
            pages, total_pages = await asyncio.to_thread(render.rasterize, str(path))
        except DetectorUnavailable as e:
            raise HTTPException(422, f"检测不可用: {e}")
        regions = []
        for name, det in dets:
            try:
                regions.extend(await asyncio.to_thread(det.detect, pages))
            except DetectorUnavailable as e:
                # fail loudly, never a silent partial: "signature detector
                # down" must not be readable as "no signature in the document"
                raise HTTPException(422, f"检测不可用({name}): {e}")

    out = []
    for r in _suppress_seal_echoes(regions):
        if r.label not in kind_set:
            continue
        page = next(p for p in pages if p.page_no == r.page)
        x0, y0, x1, y1 = r.bbox
        out.append({
            "page": r.page, "label": r.label, "score": r.score,
            # percent, top-left origin: the /locate & $hits masking contract
            "x": round(x0 / page.width * 100, 2),
            "y": round(y0 / page.height * 100, 2),
            "w": round((x1 - x0) / page.width * 100, 2),
            "h": round((y1 - y0) / page.height * 100, 2),
            # page pixels: the DocStage overlay contract (viewBox = page dims)
            "bbox_px": [round(v, 2) for v in r.bbox],
            "mask": r.mask,
        })
    out.sort(key=lambda e: (e["page"], e["y"], e["x"]))
    # requested kinds with zero regions — the /locate `misses` analog, so a
    # caller can tell "no signature found" from "signature not requested".
    # NOTE misses covers SCANNED pages only: past the _MAX_PAGES cap the
    # truncated flag is the caller's signal that later pages were never
    # examined — a silent partial scan would read as "no seal in the
    # document", which the redaction downstream cannot afford.
    misses = sorted(kind_set - {e["label"] for e in out})
    return {"page_count": total_pages, "pages_scanned": len(pages),
            "truncated": len(pages) < total_pages, "detector": "+".join(det_names),
            "pages": [{"page": p.page_no, "width": p.width, "height": p.height}
                      for p in pages],
            "regions": out, "misses": misses}
