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
            result[f.name] = rows          # table rows pass through as objects
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
