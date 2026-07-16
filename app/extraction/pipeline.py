"""Extraction pipeline (design v0.2 §5.2): UDR + SkillPackage -> result dict in
the live-verified schema {field: {"$value","$confidence","$bbox","$pages"}} with
tables as row arrays and inferred fields carrying "$reasoning"+"inferred".
Pure function of (udr, pkg) — idempotent by design (HA discipline).
"""
from app.extraction import confidence as conf
from app.extraction.provider_client import chat_json_with_fallback
from app.extraction.validators import run_validators
from app.parsers.base import UDR
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import SkillPackage


def extract(udr: UDR, pkg: SkillPackage, transport=None,
            provider_override: str | None = None) -> tuple[dict, dict, bool]:
    """Returns (result, usage, needs_review). Resilience: extractor -> fallback
    chain with cooldown (M1 acceptance hit exactly this failure mode)."""
    if provider_override:
        chain: list[str | None] = [provider_override]
    else:
        chain = [pkg.model_binding.extractor or None]
        if pkg.model_binding.fallback:
            chain.append(pkg.model_binding.fallback)
    raw, usage, used = chat_json_with_fallback(
        compile_messages(pkg, udr), chain, transport=transport)
    usage = dict(usage)
    usage["provider_used"] = used

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
