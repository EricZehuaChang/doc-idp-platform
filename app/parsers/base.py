"""UDR — Unified Document Representation (design v0.2 §4.1, aligned with
Docling's document model). Every parser outputs UDR; the extraction layer
depends only on UDR, so parsers are swappable without touching extraction.
bbox flows end-to-end: parser -> extraction backfill -> verification highlight.
"""
from pydantic import BaseModel, Field


class Block(BaseModel):
    type: str = "text"                     # text|table|figure|title
    text: str = ""
    bbox: list[float] | None = None        # [x0, y0, x1, y1] in page pixel space
    confidence: float = 1.0                # 1.0 = unknown (KBase OCR contract semantics)


class Page(BaseModel):
    page_no: int
    width: float = 0
    height: float = 0
    blocks: list[Block] = Field(default_factory=list)
    markdown: str = ""


class UDR(BaseModel):
    pages: list[Page] = Field(default_factory=list)
    full_markdown: str = ""
    parser: str = ""
    lang: list[str] = Field(default_factory=list)

    def full_text(self) -> str:
        """Plain text for the consistency channel (§5.3: value must be locatable
        in source text, otherwise suspected hallucination -> needs review)."""
        parts = [b.text for p in self.pages for b in p.blocks if b.text]
        return "\n".join(parts) if parts else self.full_markdown


class ParserUnavailable(RuntimeError):
    """Transient parser failure (network/key/format): file goes back to retry,
    not to permanent error — same semantics as KBase OCRUnavailable."""


class Parser:
    """Protocol: implementations register via registry.register("parser", name)
    and provide parse(path) -> UDR."""

    def parse(self, path: str) -> UDR:  # pragma: no cover - interface only
        raise NotImplementedError
