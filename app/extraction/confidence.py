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

from app.parsers.base import Block, UDR

_STRIP = re.compile(r"[\s,，¥￥]")


def _norm(s: str) -> str:
    return _STRIP.sub("", s or "").lower()


def _norm_map(s: str) -> tuple[str, list[int]]:
    """_norm(s) plus a map from each normalized char back to its source index —
    keeps tight boxes possible for separator-insensitive matches ("6222 0202"
    vs "62220202"). Must stay in lockstep with _norm's character class."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        if _STRIP.match(ch):
            continue
        out.append(ch.lower())
        idx.append(i)
    return "".join(out), idx


def _tight(block: Block, lo: int, hi: int) -> list[float] | None:
    """Union bbox of the glyphs spanning block.text[lo..hi] (inclusive).
    None when the block has no char map or the range holds no real glyph —
    caller falls back to the block bbox."""
    chars = block.chars
    if not chars or hi >= len(chars):
        return None
    boxes = [b for b in chars[lo:hi + 1] if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def locate_all(value: str, udr: UDR, limit: int = 20) -> tuple[list[dict], bool]:
    """Every source occurrence of value — masking needs each hit, not just the
    first. Exact pass wins outright; the normalized pass (whitespace/case/
    thousand-separator insensitive) only runs when nothing matched exactly,
    same discipline as locate(). Hits are {"page", "bbox"} with the tightest
    box available: char-union when the block carries glyph geometry, else the
    block bbox. Non-overlapping, capped at `limit` (defensive: an LLM row
    value like "。" must not explode the result payload)."""
    if not value:
        return [], False
    hits: list[dict] = []
    for page in udr.pages:
        for block in page.blocks:
            text = block.text or ""
            start = 0
            while len(hits) < limit:
                i = text.find(value, start)
                if i < 0:
                    break
                bbox = _tight(block, i, i + len(value) - 1) or block.bbox
                hits.append({"page": page.page_no, "bbox": bbox})
                start = i + len(value)
            if len(hits) >= limit:
                return hits, True
    if hits:
        return hits, True
    nval = _norm(value)
    if not nval:
        return [], False
    for page in udr.pages:
        for block in page.blocks:
            ntext, idxmap = _norm_map(block.text or "")
            start = 0
            while len(hits) < limit:
                i = ntext.find(nval, start)
                if i < 0:
                    break
                lo, hi = idxmap[i], idxmap[i + len(nval) - 1]
                bbox = _tight(block, lo, hi) or block.bbox
                hits.append({"page": page.page_no, "bbox": bbox})
                start = i + len(nval)
            if len(hits) >= limit:
                return hits, False
    return hits, False


def locate(value: str, udr: UDR) -> tuple[int | None, list[float] | None, bool]:
    """First source hit of value (single-value fields). See locate_all."""
    hits, exact = locate_all(value, udr, limit=1)
    if hits:
        return hits[0]["page"], hits[0]["bbox"], exact
    return None, None, False


def score_cell(value: str, udr: UDR) -> tuple[int, list[dict]]:
    """Table-cell variant of score_field (§5.3 applied to entity rows): no rule
    channel, multi-hit. 3 = exact hits, 2 = normalized hits, 1 = only found in
    whole-document markdown (no per-block anchor), 0 = not in source."""
    hits, exact = locate_all(value, udr)
    if hits:
        return (3 if exact else 2), hits
    nval = _norm(value)
    if nval and nval in _norm(udr.full_markdown or udr.full_text()):
        return 1, []
    return 0, []


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
