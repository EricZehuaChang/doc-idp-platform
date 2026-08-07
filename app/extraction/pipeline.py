"""Extraction pipeline (design v0.2 §5.2): UDR + SkillPackage -> result dict in
the live-verified schema {field: {"$value","$confidence","$bbox","$pages"}} with
tables as row arrays and inferred fields carrying "$reasoning"+"inferred".
Pure function of (udr, pkg) — idempotent by design (HA discipline).
"""
import re

from app.extraction import confidence as conf


def _norm(v: str) -> str:
    """Value comparison for arbitration: whitespace/case/thousand-separator
    insensitive — two models phrasing '1,026.50' vs '1026.50' still agree."""
    return re.sub(r"[\s,，]", "", v).lower()
from app.extraction.provider_client import chat_json_with_fallback
from app.extraction.validators import run_validators
from app.parsers.base import UDR
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import SkillPackage


def _percent_hits(hits: list, udr: UDR) -> list:
    """Enrich each hit with page-percent x/y/w/h (top-left origin, 0-100).

    /status carries no page dims, so a masking consumer (mask-guard route-A)
    cannot convert parser-space bbox itself — and the parser space varies
    (pdfplumber = PDF points, OCR = render pixels). Converting here, where the
    UDR is in hand, is the only place both sides of the ratio are known.
    Zero-dim pages (markitdown has no geometry) keep the bare {page, bbox}
    contract: emitting percent against a fake size would be a wrong box, and a
    wrong box gets masked as if it were right."""
    out = []
    for h in hits:
        entry = dict(h)
        bbox, page_no = h.get("bbox"), h.get("page")
        if (bbox and len(bbox) == 4 and isinstance(page_no, int)
                and 1 <= page_no <= len(udr.pages)):
            p = udr.pages[page_no - 1]
            if p.width and p.height:
                x0, top, x1, bottom = bbox
                entry.update(
                    x=round(x0 / p.width * 100, 2),
                    y=round(top / p.height * 100, 2),
                    w=round((x1 - x0) / p.width * 100, 2),
                    h=round((bottom - top) / p.height * 100, 2))
        out.append(entry)
    return out


def _locate_rows(rows: list, spec, udr: UDR) -> list:
    """Attach per-cell source locations to table rows as $-prefixed metadata
    (mirroring the scalar cell contract): row["$cells"][col] =
    {"$confidence", "$hits" [{page, bbox}...]}. Rows keep their plain column
    values — the review grid and rows PATCH are untouched; masking consumers
    read every occurrence from $hits. Inferred columns are derivations
    (nothing to locate); empty cells carry no metadata. Cell scores never
    feed the needs_review gate — masking skills enforce human review via
    review_policy.mode="always" instead (§5.6 discipline stays scalar-only)."""
    out = []
    for row in rows:
        if isinstance(row, dict):
            cells = {}
            for c in spec.columns:
                if c.mode == "inferred" or c.type == "table":
                    continue
                v = row.get(c.name)
                if v is None or isinstance(v, (dict, list)):
                    continue
                sval = str(v).strip()
                if not sval:
                    continue
                score, hits = conf.score_cell(sval, udr)
                cells[c.name] = {"$confidence": score,
                                 "$hits": _percent_hits(hits, udr)}
            if cells:
                row = {**row, "$cells": cells}
        out.append(row)
    return out


def extract(udr: UDR, pkg: SkillPackage, transport=None,
            provider_override: str | None = None) -> tuple[dict, dict, bool]:
    """Returns (result, usage, needs_review). Resilience: extractor -> fallback
    chain with cooldown (M1 acceptance hit exactly this failure mode).
    Challenger arbitration (§5.3 model channel): a second model re-extracts and
    disagreements are forced into human review."""
    if provider_override:
        chain: list[str | None] = [provider_override]
    else:
        chain = [pkg.model_binding.extractor or None]
        if pkg.model_binding.fallback:
            chain.append(pkg.model_binding.fallback)
    messages = compile_messages(pkg, udr)
    raw, usage, used = chat_json_with_fallback(messages, chain, transport=transport)
    usage = dict(usage)
    usage["provider_used"] = used

    # challenger pass (skipped for dry-run overrides: they compare providers
    # explicitly). Best-effort: an unavailable challenger never fails the file.
    challenger_raw: dict | None = None
    challenger = pkg.model_binding.challenger
    if challenger and not provider_override and challenger != used:
        try:
            challenger_raw, ch_usage, _ = chat_json_with_fallback(
                messages, [challenger], transport=transport)
            usage["challenger_used"] = challenger
            usage["challenger_prompt_tokens"] = int(ch_usage.get("prompt_tokens") or 0)
            usage["challenger_completion_tokens"] = int(ch_usage.get("completion_tokens") or 0)
        except Exception as e:      # arbitration degraded, primary result stands
            usage["challenger_error"] = str(e)[:200]

    # flatten for the rule channel: inferred fields arrive as {value, reasoning}
    flat: dict[str, object] = {}
    for f in pkg.fields:
        v = raw.get(f.name)
        if f.mode == "inferred" and isinstance(v, dict):
            flat[f.name] = v.get("value")
        else:
            flat[f.name] = v
    rule_failures = run_validators(flat, pkg.validators)

    result: dict[str, object] = {}
    lowest = 3
    for f in pkg.fields:
        raw_val = raw.get(f.name)
        if f.type == "table":
            rows = raw_val if isinstance(raw_val, list) else []
            result[f.name] = _locate_rows(rows, f, udr)
            continue
        reasoning = None
        if f.mode == "inferred" and isinstance(raw_val, dict):
            value = str(raw_val.get("value") or "")
            reasoning = str(raw_val.get("reasoning") or "")
        else:
            value = "" if raw_val is None else str(raw_val)
        score, page, bbox = conf.score_field(
            value=value, udr=udr,
            rule_failed=f.name in rule_failures,
            inferred=(f.mode == "inferred"),
            has_reasoning=bool(reasoning))
        cell: dict[str, object] = {
            "$value": value, "$confidence": score,
            "$bbox": bbox or [], "$pages": page or "",
        }
        # model channel (§5.3): challenger disagreement caps confidence at 1
        # (below any sane threshold) and records the second opinion
        if challenger_raw is not None:
            ch_val = challenger_raw.get(f.name)
            if f.mode == "inferred" and isinstance(ch_val, dict):
                ch_val = ch_val.get("value")
            ch_str = "" if ch_val is None else str(ch_val)
            agree = _norm(ch_str) == _norm(value)
            if not agree:
                score = min(score, 1)
                cell["$confidence"] = score
            cell["$challenger"] = {"value": ch_str, "agree": agree}
        if f.mode == "inferred":
            cell["inferred"] = True        # exports must distinguish extracted vs derived (§5.6)
            cell["$reasoning"] = reasoning or ""
        if f.name in rule_failures:
            cell["$rule_failures"] = rule_failures[f.name]
        result[f.name] = cell
        lowest = min(lowest, score)

    policy = pkg.review_policy
    if policy.mode == "always":
        needs_review = True
    elif policy.mode == "never":
        needs_review = False
    else:
        needs_review = lowest < policy.confidence_threshold
    return result, usage, needs_review
