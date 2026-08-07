"""Red-seal CV detector (integration design 2026-08-07 §2.2, optional cascade).
Zero-model, CPU-milliseconds: HSV double-band red threshold -> dilation ->
connected components -> box filters. Pillow + numpy only (no OpenCV dep).

Role: first-pass sieve and red-seal fallback that works with NO weights on
disk — /detect?detector=red-seal-cv is functional the day the code lands.
By nature red-only: black/blue seals and signatures need pp-doclayout.
Filters are tuned for "cover the whole stamp" recall (redaction channel):
boxes get a safety margin (§三 分割掩膜+安全膨胀 rationale, box edition).
"""
from app.detectors.base import DetectorUnavailable, PageImage, Region
from app.plugins.registry import registry

_WORK = 900             # analysis resolution cap (labeling cost bound)
# PIL HSV is 0-255 per channel: hue red bands ≈ <17° or >341°
_H_LO, _H_HI = 12, 242
_S_MIN, _V_MIN = 70, 60
_DUST_FRAC = 0.00005     # pre-merge dust cut (noise specks)
_MIN_AREA_FRAC = 0.0005  # merged box < 0.05% of page = red text/noise
_MAX_AREA_FRAC = 0.5     # a seal never covers half the page (red form paper)
_MAX_ASPECT = 4.0        # seals are round/oval/rect-ish, not lines
_MIN_FILL = 0.15         # thin red rules/borders have low bbox fill
_MARGIN = 0.03           # per-side safety margin, fraction of box size
# glyph-run gluing gap: 1.2% of page width. Measured on the 太和县 red-header
# sample: header glyph gaps run up to ~0.95%, adjacent seal gaps are >=1.9% —
# glue glyph runs into strips (aspect filter then rejects them) while keeping
# side-by-side seals apart; and if two seals ever DO merge, the union box
# still covers both, which the redaction channel tolerates. Known residual:
# isolated huge glyphs (红头 "文件") can still pass — red-header documents
# should use the pp-doclayout detector, this one is the red-seal fallback.
_GLUE_GAP_FRAC = 0.012
_ABSORB_OVERLAP = 0.25   # overlap/min-area above this = fragments of one stamp


def _red_mask(img):
    """RGB PIL image -> dilated boolean red mask (numpy, work resolution)."""
    import numpy as np
    from PIL import Image, ImageFilter
    hsv = np.asarray(img.convert("HSV"))
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((h <= _H_LO) | (h >= _H_HI)) & (s >= _S_MIN) & (v >= _V_MIN)
    # dilation bridges ring/star/text fragments of one stamp into one blob
    dil = Image.fromarray((mask * 255).astype("uint8")).filter(
        ImageFilter.MaxFilter(5))
    return np.asarray(dil) > 0


def _components(mask):
    """4-connected components via BFS -> list of (area, x0, y0, x1, y1).
    Bounded by _WORK resolution so pure-Python flood fill stays cheap."""
    import numpy as np
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=bool)   # visited
    comps = []
    for sy, sx in zip(*np.nonzero(mask)):
        if labels[sy, sx]:
            continue
        stack = [(int(sy), int(sx))]
        labels[sy, sx] = True
        area, x0, y0, x1, y1 = 0, int(sx), int(sy), int(sx), int(sy)
        while stack:
            cy, cx = stack.pop()
            area += 1
            x0, x1 = min(x0, cx), max(x1, cx)
            y0, y1 = min(y0, cy), max(y1, cy)
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = True
                    stack.append((ny, nx))
        comps.append((area, x0, y0, x1 + 1, y1 + 1))
    return comps


def _should_merge(a, b, gap_x: float) -> bool:
    """Two component boxes belong together when they overlap enough to be
    fragments of one stamp (ring/star/text of a seal label as separate
    blobs), or when they sit on one text line with a glyph-sized gap —
    letterhead runs then become wide strips the aspect filter rejects."""
    _, ax0, ay0, ax1, ay1 = a
    _, bx0, by0, bx1, by1 = b
    ox = min(ax1, bx1) - max(ax0, bx0)     # x overlap; negative = gap
    oy = min(ay1, by1) - max(ay0, by0)
    if ox > 0 and oy > 0:
        amin = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
        if ox * oy / amin >= _ABSORB_OVERLAP:
            return True
    return ox >= -gap_x and oy >= 0.5 * min(ay1 - ay0, by1 - by0)


def _merge_boxes(boxes, gap_x: float):
    """Union-merge to fixpoint. Entries are (area, x0, y0, x1, y1); merged
    area is the component-pixel sum so the fill filter sees true redness."""
    changed = True
    while changed:
        changed = False
        out = []
        for b in boxes:
            for i, o in enumerate(out):
                if _should_merge(b, o, gap_x):
                    out[i] = (o[0] + b[0], min(o[1], b[1]), min(o[2], b[2]),
                              max(o[3], b[3]), max(o[4], b[4]))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


@registry.register("detector", "red-seal-cv")
class RedSealCvDetector:
    def detect(self, pages: list[PageImage]) -> list[Region]:
        try:
            import numpy  # noqa: F401  probe the vision extra before work
        except ImportError as e:
            raise DetectorUnavailable(
                "numpy/pillow not installed — pip install '.[vision]'") from e
        regions: list[Region] = []
        for page in pages:
            scale = max(page.image.width, page.image.height) / _WORK
            # max(1,...): an extreme-aspect image (e.g. 3000x1) must not round
            # its short axis to 0 — PIL resize would raise, surfacing as a 500
            work = page.image if scale <= 1 else page.image.resize(
                (max(1, round(page.image.width / scale)),
                 max(1, round(page.image.height / scale))))
            mask = _red_mask(work)
            page_area = mask.shape[0] * mask.shape[1]
            up = page.width / mask.shape[1]      # work px -> page px
            boxes = [b for b in _components(mask)
                     if b[0] / page_area >= _DUST_FRAC]
            boxes = _merge_boxes(boxes, gap_x=_GLUE_GAP_FRAC * mask.shape[1])
            for area, x0, y0, x1, y1 in boxes:
                w, h = x1 - x0, y1 - y0
                frac = area / page_area
                fill = area / (w * h)
                if not (_MIN_AREA_FRAC <= frac <= _MAX_AREA_FRAC):
                    continue
                if max(w / h, h / w) > _MAX_ASPECT or fill < _MIN_FILL:
                    continue
                mx, my = w * up * _MARGIN, h * up * _MARGIN
                regions.append(Region(
                    page=page.page_no, label="seal",
                    bbox=[max(0.0, x0 * up - mx), max(0.0, y0 * up - my),
                          min(page.width, x1 * up + mx),
                          min(page.height, y1 * up + my)],
                    # heuristic confidence: how solidly red the box is
                    score=round(min(0.95, max(0.3, fill)), 2)))
        return regions
