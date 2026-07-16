"""Format normalization + auto routing (design v0.2 §4.3): electronic files go
to text-layer parsers, images/scans go to the tier default OCR parser. A skill
may pin a parser explicitly (skill package "parser" field) which wins over auto.
OFD three-level handling and multi-doc split land in M2.
"""
from pathlib import Path

from app.config import get_settings, load_parsers
from app.parsers import electronic, glm_ocr_cloud, rapidocr_http  # noqa: F401  register plugins
from app.parsers.base import UDR, Parser, ParserUnavailable
from app.plugins.registry import registry

_OFFICE = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"}
_IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _make(name: str) -> Parser:
    cfg = load_parsers()["parsers"].get(name)
    kwargs = {}
    if cfg and cfg.type == "cloud_api":
        kwargs = {"base_url": cfg.base_url, "api_key_env": cfg.api_key_env}
    return registry.create("parser", name, **kwargs)


def default_scan_parser() -> str:
    tier = get_settings().deploy_tier
    return load_parsers()["default_parser"].get(tier, "glm-ocr-cloud")


def _scan_parse(path: str) -> UDR:
    """Degradation chain (HA design v2.0 §2.5): tier default OCR first,
    RapidOCR CPU as last resort. Degradation is logged by the caller via
    UDR.parser so quality gates can see which engine produced the text."""
    try:
        return _make(default_scan_parser()).parse(path)
    except ParserUnavailable:
        return _make("rapidocr").parse(path)


def parse_document(path: str, pinned_parser: str | None = None) -> UDR:
    if pinned_parser:
        return _make(pinned_parser).parse(path)
    suffix = Path(path).suffix.lower()
    if suffix in _OFFICE:
        return _make("markitdown").parse(path)
    if suffix == ".pdf":
        try:
            return _make("pdfplumber").parse(path)   # text layer first
        except ParserUnavailable:
            return _scan_parse(path)                 # scanned PDF -> OCR chain
    if suffix in _IMAGES:
        return _scan_parse(path)
    raise ParserUnavailable(f"unsupported file type: {suffix}")
