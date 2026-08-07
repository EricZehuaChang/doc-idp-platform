"""Shared ONNX runtime plumbing for detector plugins: a process-wide,
thread-safe, load-once session cache. Model weights are NOT pip-shipped —
ops seed them from the configured model_source (air_gapped MUST pre-seed),
and a missing file degrades to DetectorUnavailable -> explicit 422.
"""
import threading
from pathlib import Path

from app.config import REPO_ROOT
from app.detectors.base import DetectorUnavailable

_lock = threading.Lock()
_session_cache: dict[str, object] = {}   # resolved model path -> ort.InferenceSession


def resolve_model_path(model_path: str) -> Path:
    p = Path(model_path)
    return p if p.is_absolute() else REPO_ROOT / p


def load_session(model_path: str, model_source: str = ""):
    """Load-once onnxruntime CPU session for the given weights file."""
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise DetectorUnavailable(
            "onnxruntime not installed — pip install '.[vision]'") from e
    path = resolve_model_path(model_path)
    key = str(path)
    with _lock:
        sess = _session_cache.get(key)
        if sess is None:
            if not path.exists():
                hint = f",从 {model_source} 下载" if model_source else ""
                raise DetectorUnavailable(
                    f"模型权重缺失: {path}{hint} 后重试(air_gapped 部署须预置)")
            try:
                sess = ort.InferenceSession(key, providers=["CPUExecutionProvider"])
            except Exception as e:
                raise DetectorUnavailable(f"cannot load onnx model: {e}") from e
            _session_cache[key] = sess
    return sess
