"""YOLOS signature detector (design 2026-08-07 §5 phase 5 — the independent
signature detector that PP-DocLayoutV3 cannot provide, since the official
25-class set has no signature class; this closes Insavlo's last exclusive
capability, SIGNATURE visual detection).

Model: onnx-community/yolos-base-signature-detection-ONNX — the official
onnx-community conversion of the YOLOS signature detector trained on the
tech4humans/signature-detection dataset. Apache-2.0 code AND weights. YOLOS
is the hustvl ViT-based detector (arXiv:2106.00666) — NOT an Ultralytics
YOLO, so no AGPL contamination.

Verified export contract (repo config.json + preprocessor_config.json,
checked 2026-08-07):
- input:  pixel_values [1,3,640,640] f32 (RGB, /255 then ImageNet mean/std;
  fixed 640x640 resize, no aspect preservation)
- output: logits [1,100,2] (class 0 = signature, last class = no-object,
  softmax over the last dim) + pred_boxes [1,100,4] cxcywh NORMALIZED to
  the input — a fixed resize maps linearly back, so boxes scale straight
  to original page pixels.
"""
from app.detectors import onnx_util
from app.detectors.base import DetectorUnavailable, PageImage, Region
from app.plugins.registry import registry

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_INPUT = 640


def _boxes_to_regions(scores, boxes, threshold: float, label: str,
                      page: PageImage) -> list[Region]:
    """Per-query (score, cxcywh-normalized box) -> Regions: score gate,
    denormalize to page pixels, clamp. Pure function for model-free tests."""
    out: list[Region] = []
    for score, (cx, cy, w, h) in zip(scores, boxes):
        if score < threshold:
            continue
        x0 = min(max((cx - w / 2) * page.width, 0.0), page.width)
        y0 = min(max((cy - h / 2) * page.height, 0.0), page.height)
        x1 = min(max((cx + w / 2) * page.width, 0.0), page.width)
        y1 = min(max((cy + h / 2) * page.height, 0.0), page.height)
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        out.append(Region(page=page.page_no, label=label,
                          bbox=[x0, y0, x1, y1], score=round(float(score), 4)))
    return out


@registry.register("detector", "yolos-signature")
class YolosSignatureDetector:
    def __init__(self, model_path: str = "data/models/yolos-signature.onnx",
                 model_source: str = "", score_threshold: float = 0.5,
                 class_map: dict[int, str] | None = None):
        self.model_path = model_path
        self.model_source = model_source
        self.score_threshold = score_threshold
        # single-class model: class 0 (id2label) — configurable for symmetry
        self.class_map = {int(k): v for k, v in (class_map or {0: "signature"}).items()}

    def _preprocess(self, page: PageImage):
        import numpy as np
        resized = page.image.resize((_INPUT, _INPUT))
        blob = np.asarray(resized, dtype=np.float32) / 255.0
        blob = (blob - np.array(_IMAGENET_MEAN, np.float32)) \
            / np.array(_IMAGENET_STD, np.float32)
        return blob.transpose(2, 0, 1)[np.newaxis, ...]

    def detect(self, pages: list[PageImage]) -> list[Region]:
        import numpy as np
        sess = onnx_util.load_session(self.model_path, self.model_source)
        input_name = sess.get_inputs()[0].name        # "pixel_values"
        label = self.class_map.get(0, "signature")
        regions: list[Region] = []
        for page in pages:
            try:
                logits, boxes = sess.run(None, {input_name: self._preprocess(page)})
            except Exception as e:
                raise DetectorUnavailable(f"onnx inference failed: {e}") from e
            if getattr(logits, "ndim", 0) != 3 or getattr(boxes, "ndim", 0) != 3 \
                    or boxes.shape[-1] != 4:
                raise DetectorUnavailable(
                    f"unrecognized model output shapes {logits.shape}/{boxes.shape}"
                    " — expected logits [1,N,C] + pred_boxes [1,N,4]")
            # softmax over classes; last class is DETR-family "no object"
            z = logits[0] - logits[0].max(axis=-1, keepdims=True)
            probs = np.exp(z)
            probs /= probs.sum(axis=-1, keepdims=True)
            regions.extend(_boxes_to_regions(
                probs[:, 0], boxes[0], self.score_threshold, label, page))
        return regions
