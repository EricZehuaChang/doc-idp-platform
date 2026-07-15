"""Consistency channel + confidence synthesis (design v0.2 §5.3).
We never trust the model's self-reported probability. Confidence is 0-3
(live-verified Insavlo scale, sources/2026-07-14):
  3 = located in source AND passed all rules
  2 = located, no rules applicable
  1 = weakly located (normalized match)
  0 = not locatable (suspected hallucination) or rule failure -> needs review
Inferred-mode fields skip source location (§5.6) but must carry reasoning.
"""
import re

from app.parsers.base import UDR


def _norm(s: str) -> str:
    return re.sub(r"[\s,，¥￥]", "", s or "").lower()


def locate(value: str, udr: UDR) -> tuple[int | None, list[float] | None, bool]:
    """Find value in UDR blocks. Returns (page_no, bbox, exact)."""
    if not value:
        return None, None, False
    for page in udr.pages:
        for block in page.blocks:
            if value in (block.text or ""):
                return page.page_no, block.bbox, True
    nval = _norm(value)
    if not nval:
        return None, None, False
    for page in udr.pages:
        for block in page.blocks:
            if nval in _norm(block.text):
                return page.page_no, block.bbox, False
    # fall back to whole-document markdown (parsers without block granularity)
    if nval in _norm(udr.full_markdown):
        return None, None, False
    return None, None, False


def score_field(value: str, udr: UDR, rule_failed: bool, inferred: bool,
                has_reasoning: bool) -> tuple[int, int | None, list[float] | None]:
    """Returns (confidence 0-3, page_no, bbox)."""
    if rule_failed:
        return 0, None, None
    if inferred:
        # inferred fields: no source location required; reasoning is mandatory
        return (2 if has_reasoning else 0), None, None
    if not value:                      # empty verbatim value: honest but reviewable
        return 1, None, None
    page, bbox, exact = locate(value, udr)
    if page is None and bbox is None and not exact:
        located_anywhere = _norm(value) in _norm(udr.full_markdown or udr.full_text())
        return (1, None, None) if located_anywhere else (0, None, None)
    return (3 if exact else 2), page, bbox
