"""Skill Studio services (design v0.2 §5.1 self-serve loop):
- probe: sample document -> LLM-drafted field list (the "80% pre-annotated" start)
- dry_run: sample + package -> extraction result without creating a task;
  supports multiple providers side-by-side (Unstract-validated interaction)
- golden_check: run a package over the skill's golden samples and diff against
  expectations (publish gate data, PM item #4)
"""
import json

from app.extraction.pipeline import extract
from app.extraction.provider_client import chat_json_with_fallback
from app.parsers.base import UDR
from app.skillengine.schema import FieldSpec, SkillPackage

_PROBE_PROMPT = (
    "你是文档抽取技能设计助手。分析给定文档内容，产出建议抽取的字段草稿。\n"
    "输出 JSON：{\"doc_type\": \"文档类型\", \"fields\": [{\"name\": \"英文snake_case字段名\","
    " \"label\": \"中文名\", \"type\": \"string|number|date|enum|table\","
    " \"instruction\": \"一句话抽取规则\", \"sample_value\": \"该字段在本样本中的值(如可见)\"}]}\n"
    "规则：字段覆盖表头关键信息；重复行记录归纳为一个 table 字段；不超过 15 个字段；只输出 JSON。"
)


def probe(udr: UDR, provider: str | None = None, transport=None) -> dict:
    """Sample -> drafted fields. Returned shape feeds the skill editor directly."""
    doc = (udr.full_markdown or udr.full_text())[:16000]
    raw, usage, used = chat_json_with_fallback(
        [{"role": "system", "content": _PROBE_PROMPT},
         {"role": "user", "content": doc}],
        [provider], transport=transport)
    return {"draft": raw, "usage": usage, "provider_used": used}


def dry_run(udr: UDR, pkg: SkillPackage, providers: list[str] | None = None,
            transport=None) -> list[dict]:
    """Run extraction over one sample with 1..N providers side-by-side."""
    runs = providers or [pkg.model_binding.extractor or ""]
    out = []
    for p in runs:
        try:
            result, usage, needs_review = extract(
                udr, pkg, transport=transport, provider_override=p or None)
            out.append({"provider": p or "(active)", "ok": True,
                        "needs_review": needs_review, "usage": usage,
                        "result": result})
        except Exception as e:
            out.append({"provider": p or "(active)", "ok": False,
                        "error": str(e)[:300]})
    return out


def _cell_value(v) -> str:
    if isinstance(v, dict):
        return str(v.get("$value") or "")
    if isinstance(v, list):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return "" if v is None else str(v)


def diff_results(expected: dict, actual: dict) -> dict:
    """Field-level diff: expected (golden, plain values) vs actual (pipeline
    result cells). Returns {field: {expected, actual, match}} + summary."""
    fields = {}
    matches = 0
    keys = set(expected) | {k for k in actual}
    for k in sorted(keys):
        exp = str(expected.get(k, "") or "")
        act = _cell_value(actual.get(k))
        ok = exp.strip() == act.strip()
        matches += ok
        fields[k] = {"expected": exp, "actual": act, "match": ok}
    total = len(keys) or 1
    return {"fields": fields, "match_rate": round(matches / total, 3),
            "total": len(keys), "matched": matches}


def golden_check(pkg: SkillPackage, samples: list[tuple[UDR, dict]],
                 transport=None) -> dict:
    """Publish-gate data: run pkg over golden samples, diff each against its
    expected values. Caller decides whether to block publish on low match."""
    reports = []
    for udr, expected in samples:
        try:
            result, usage, _ = extract(udr, pkg, transport=transport)
            reports.append({"ok": True, **diff_results(expected, result)})
        except Exception as e:
            reports.append({"ok": False, "error": str(e)[:300]})
    rates = [r["match_rate"] for r in reports if r.get("ok")]
    return {"samples": len(samples), "reports": reports,
            "avg_match_rate": round(sum(rates) / len(rates), 3) if rates else 0.0}


def package_to_yaml(pkg: SkillPackage) -> str:
    import yaml
    return yaml.safe_dump(pkg.model_dump(), allow_unicode=True, sort_keys=False)


def package_from_yaml(text: str) -> SkillPackage:
    import yaml
    return SkillPackage(**yaml.safe_load(text))


def draft_to_fields(draft: dict) -> list[FieldSpec]:
    """Probe draft -> FieldSpec list (editor prefill)."""
    fields = []
    for f in draft.get("fields", []):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        fields.append(FieldSpec(
            name=str(f["name"]), type=str(f.get("type") or "string"),
            instruction=str(f.get("instruction") or f.get("label") or "")))
    return fields
