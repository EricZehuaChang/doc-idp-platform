"""PP-DocLayoutV3 ONNX detector (integration design 2026-08-07 §2.2).
Layout model whose class set includes `seal` (id 20); Apache-2.0 code AND
weights — the license property that disqualified the Ultralytics family.

Verified export contract (alex-dinh/PP-DocLayoutV3-ONNX README + official
PaddlePaddle/PP-DocLayoutV3_safetensors config.json, checked 2026-08-07):
- inputs:  im_shape [[H,W]] f32; image [1,3,800,800] f32 (RGB, /255 then
  ImageNet mean/std); scale_factor [[target/orig_h, target/orig_w]] f32
- output:  (N,7) rows [label_index, score, xmin, ymin, xmax, ymax,
  read_order], boxes already in ORIGINAL image pixel space (the graph
  divides by scale_factor internally) — no mask output in this export,
  so phase 1 ships rectangles (mask refinement is phase 4).
- official 25-class id2label has NO signature class (design-doc correction:
  signature needs its own detector, phase 5). class_map stays config-driven
  so a re-export or fine-tune only touches configs/detectors.yaml.

The ORT session is cached process-wide via onnx_util (same lazy-client
discipline as parsers/glm_ocr_cloud.py) — model load is ~seconds, requests ~ms.
"""
from app.detectors import onnx_util
from app.detectors.base import DetectorUnavailable, PageImage, Region
from app.plugins.registry import registry

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_DEFAULT_INPUT = 800                     # fallback when the graph is dynamic


def _rows_to_regions(rows, class_map: dict[int, str], threshold: float,
                     page: PageImage) -> list[Region]:
    """(N,>=6) rows -> Regions: score gate, class filter, clamp to page.
    Pure function so tests exercise post-processing without onnxruntime."""
    out: list[Region] = []
    for row in rows:
        cls, score = int(row[0]), float(row[1])
        label = class_map.get(cls)
        if label is None or score < threshold:
            continue
        x0 = min(max(float(row[2]), 0.0), page.width)
        y0 = min(max(float(row[3]), 0.0), page.height)
        x1 = min(max(float(row[4]), 0.0), page.width)
        y1 = min(max(float(row[5]), 0.0), page.height)
        if x1 - x0 < 1 or y1 - y0 < 1:   # degenerate box after clamping
            continue
        out.append(Region(page=page.page_no, label=label,
                          bbox=[x0, y0, x1, y1], score=round(score, 4)))
    return out


@registry.register("detector", "pp-doclayout")
class PPDocLayoutDetector:
    def __init__(self, model_path: str = "data/models/PP-DocLayoutV3.onnx",
                 model_source: str = "", score_threshold: float = 0.5,
                 class_map: dict[int, str] | None = None):
        self.model_path = model_path
        self.model_source = model_source
        self.score_threshold = score_threshold
        self.class_map = {int(k): v for k, v in (class_map or {20: "seal"}).items()}

    def _input_size(self, sess) -> int:
        """Static H from the 4-D image input ([1,3,800,800]); 800 if dynamic."""
        for inp in sess.get_inputs():
            shape = inp.shape
            if len(shape) == 4 and isinstance(shape[2], int):
                return shape[2]
        return _DEFAULT_INPUT

    def _preprocess(self, page: PageImage, size: int):
        """RGB page -> normalized CHW blob + paddle scale factors
        (scale = network / original; no aspect-ratio preservation)."""
        import numpy as np
        resized = page.image.resize((size, size))     # PIL bilinear default
        blob = np.asarray(resized, dtype=np.float32) / 255.0
        blob = (blob - np.array(_IMAGENET_MEAN, np.float32)) \
            / np.array(_IMAGENET_STD, np.float32)
        blob = blob.transpose(2, 0, 1)[np.newaxis, ...]
        return blob, size / page.height, size / page.width

    def detect(self, pages: list[PageImage]) -> list[Region]:
        import numpy as np
        sess = onnx_util.load_session(self.model_path, self.model_source)
        size = self._input_size(sess)
        input_names = [i.name for i in sess.get_inputs()]
        regions: list[Region] = []
        for page in pages:
            blob, scale_h, scale_w = self._preprocess(page, size)
            feeds = {}
            for name in input_names:
                # feed by name; paddle2onnx exports use exactly these three
                if name == "image" or name == "x":
                    feeds[name] = blob
                elif name == "im_shape":
                    feeds[name] = np.array([[size, size]], np.float32)
                elif name == "scale_factor":
                    feeds[name] = np.array([[scale_h, scale_w]], np.float32)
                else:
                    raise DetectorUnavailable(
                        f"unexpected onnx input '{name}' — export contract "
                        "changed, re-verify against README/canary")
            try:
                outputs = sess.run(None, feeds)
            except Exception as e:
                raise DetectorUnavailable(f"onnx inference failed: {e}") from e
            rows = outputs[0]
            if getattr(rows, "ndim", 0) != 2 or rows.shape[-1] < 6:
                raise DetectorUnavailable(
                    f"unrecognized model output shape {getattr(rows, 'shape', '?')}"
                    " — expected (N,7) [cls,score,x0,y0,x1,y1,order]")
            regions.extend(_rows_to_regions(
                rows, self.class_map, self.score_threshold, page))
        return regions
