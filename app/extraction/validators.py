"""Rule channel of the confidence engine (design v0.2 §5.3 channel 1):
deterministic business checks — regex/required/arithmetic. A failing rule marks
the field low-confidence regardless of what the model claims.
"""
import re
from decimal import Decimal, InvalidOperation

from app.skillengine.schema import Validator


def _to_number(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("¥", "").replace("￥", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def run_validators(values: dict[str, object], validators: list[Validator]) -> dict[str, list[str]]:
    """Returns {field_name: [failure messages]}; empty dict = all rules pass."""
    failures: dict[str, list[str]] = {}

    def fail(field: str, msg: str) -> None:
        failures.setdefault(field, []).append(msg)

    for v in validators:
        if v.type == "required" and v.field:
            val = values.get(v.field)
            if val is None or str(val).strip() == "":
                fail(v.field, "required field is empty")
        elif v.type == "regex" and v.field and v.pattern:
            val = str(values.get(v.field) or "")
            if val and re.fullmatch(v.pattern, val) is None:
                fail(v.field, f"regex mismatch: {v.pattern}")
        elif v.type == "sum_equals" and v.target and v.parts:
            target = _to_number(values.get(v.target))
            parts = [_to_number(values.get(p)) for p in v.parts]
            if target is not None and all(p is not None for p in parts):
                if abs(target - sum(parts)) > Decimal("0.01"):
                    fail(v.target, f"sum check failed: {v.target} != {'+'.join(v.parts)}")
    return failures
