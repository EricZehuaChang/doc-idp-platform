"""Detector plugin contracts — the visual twin of the parser contract
(integration design 2026-08-07 §2.2). A detector is a fixed vision model that
locates regions (seal/signature) for redaction; it never reads text, never
calls an LLM, and never touches the skill engine. Region bbox lives in the
SAME page-pixel space that render.py rasterized the page at, so width/height
of the PageImage ARE the coordinate base for percent conversion downstream.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # keep this module import-light: PIL only needed at runtime
    from PIL.Image import Image


@dataclass
class PageImage:
    """One rasterized page. width/height duplicate image.size as floats so
    coordinate math never touches the (heavy) image object."""
    page_no: int
    image: "Image"                         # RGB
    width: float
    height: float


class Region(BaseModel):
    page: int
    label: str                             # seal | signature
    bbox: list[float]                      # [x0, y0, x1, y1] page pixels, top-left origin
    score: float = 0.0                     # detector confidence 0-1
    # optional polygon [[x, y], ...] for mask-tight redaction; None = box only
    # (the current ONNX export emits boxes only — mask refinement is phase 4)
    mask: list[list[float]] | None = None


class DetectorUnavailable(RuntimeError):
    """Model weights / runtime missing or unusable: the API degrades to an
    explicit 422, never a silent empty result — same semantics as
    ParserUnavailable (parsers/base.py)."""


class Detector:
    """Protocol: implementations register via registry.register("detector", name)
    and provide detect(pages) -> list[Region]."""

    def detect(self, pages: list[PageImage]) -> list[Region]:  # pragma: no cover
        raise NotImplementedError
