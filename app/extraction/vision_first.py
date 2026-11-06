"""PR-B: an opted-in skill reads images in its first extraction pass."""

from copy import deepcopy

from app.config import get_settings, load_parsers
from app.extraction import custom_providers
from app.extraction.pipeline import extract
from app.extraction.provider_client import ProviderError
from app.extraction.validators import run_validators
from app.parsers.base import UDR
from app.skillengine.schema import SkillPackage


def vision_provider(pkg: SkillPackage, tenant: str) -> str | None:
    """Only trusted cloud-VLM mappings or explicitly marked custom channels accept images."""
    parsers = load_parsers()["parsers"]
    trusted = {cfg.provider or cfg.name for cfg in parsers.values() if cfg.type == "cloud_vlm"}
    pinned = parsers.get(pkg.parser) if pkg.parser else None
    candidates = [
        pkg.model_binding.extractor,
        pinned.provider or pinned.name if pinned and pinned.type == "cloud_vlm" else None,
        get_settings().vision_first_provider or get_settings().fast_vision_provider,
    ]
    for name in candidates:
        if not name:
            continue
        custom = custom_providers.get(tenant, name)
        if name in trusted or (custom and custom.get("vision")):
            return name
    return next((name for name in custom_providers.names(tenant)
                 if (custom_providers.get(tenant, name) or {}).get("vision")), None)


def unavailable_result(pkg: SkillPackage, reason: str):
    """A failed image pass remains visible to a reviewer with the configured shape."""
    cells = {field.name: [] if field.type == "table" else {
        "$value": "", "$confidence": 0, "$bbox": [], "$pages": ""}
        for field in pkg.fields}
    result = {"records": []} if pkg.output_shape == "list" else cells
    return result, {"prompt_tokens": 0, "completion_tokens": 0,
                    "review_reasons": [reason], "vision_first": {"attempted": False, "reason": reason}}, True


def extract_from_images(udr: UDR, pkg: SkillPackage, images: dict[int, str] | None,
                        tenant: str, transport=None):
    """One extraction pass; failure goes to review without a text-only fallback."""
    provider = vision_provider(pkg, tenant)
    if not provider or not images or set(images) != {page.page_no for page in udr.pages}:
        return unavailable_result(pkg, "vision_first_unavailable")
    execution = deepcopy(pkg)
    # The user selected one image pass and human review for exceptions (B2 override).
    execution.model_binding.challenger = None
    try:
        result, usage, review = extract(udr, execution, transport=transport,
                                       provider_override=provider, page_images=images, single_call=True)
    except (ProviderError, ValueError):
        result, usage, review = unavailable_result(pkg, "vision_first_failed")
        usage["vision_first"] = {"attempted": True, "provider": provider, "reason": "vision_first_failed"}
        return result, usage, review
    records = result.get("records", []) if pkg.output_shape == "list" else [result]
    reasons = list(usage.get("review_reasons") or [])
    for record in records:
        values = {name: cell.get("$value") if isinstance(cell, dict) else cell
                  for name, cell in record.items() if not name.startswith("$")}
        product_rules = [rule for rule in pkg.validators if rule.type == "row_product"]
        failures = run_validators(values, product_rules)
        if failures:
            reasons.append("table_arithmetic_mismatch")
            review = True
            for rule in product_rules:
                for index, row in enumerate(record.get(rule.field) or []):
                    path = f"{rule.field}[{index}].{rule.target}"
                    if path in failures:
                        cell = row.setdefault("$cells", {}).setdefault(rule.target, {})
                        cell["$rule_failures"] = failures[path]
                        if cell.get("$confidence") is not None:
                            cell["$confidence"] = min(cell["$confidence"], 1)
        for field in pkg.fields:
            value = record.get(field.name)
            if field.type != "table" and isinstance(value, dict) and "$value" in value:
                value["$source"] = "vision"
            elif field.type == "table" and isinstance(value, list):
                for row in value:
                    columns = [key for key in row if not key.startswith("$")]
                    for column in columns:
                        row.setdefault("$cells", {}).setdefault(column, {})["$source"] = "vision"
    if reasons:
        usage["review_reasons"] = list(dict.fromkeys(reasons))
    usage["vision_first"] = {"attempted": True, "provider": provider}
    return result, usage, review
