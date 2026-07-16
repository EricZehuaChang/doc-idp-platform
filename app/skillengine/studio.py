"""Skill Studio services (design v0.2 §5.1 self-serve loop):
- probe: sample document -> LLM-drafted field list (the "80% pre-annotated" start)
- dry_run: sample + package -> extraction result without creating a task;
  supports multiple providers side-by-side (Unstract-validated interaction)
- golden_check: run a package over the skill's golden samples and diff against
  expectations (publish gate data, PM item #4)
"""
import json

from app.config import load_providers
from app.extraction.pipeline import extract
from app.extraction.provider_client import chat_json_with_fallback
from app.parsers.base import UDR
from app.skillengine.schema import FieldSpec, SkillPackage


def _studio_chain(provider: str | None) -> list[str | None]:
    """Studio tools get the platform failover (M2 resilience): explicit
    provider = respect it; default = active channel + full fallback chain."""
    if provider:
        return [provider]
    return [None, *load_providers().get("fallback", [])]

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
        _studio_chain(provider), transport=transport)
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
    """Probe/text draft -> FieldSpec list (editor prefill)."""
    fields = []
    for f in draft.get("fields", []):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        cols = [FieldSpec(name=str(c["name"]),
                          type=_norm_type(c.get("type")),
                          instruction=str(c.get("instruction") or c.get("label") or ""))
                for c in (f.get("columns") or []) if isinstance(c, dict) and c.get("name")]
        fields.append(FieldSpec(
            name=str(f["name"]), type=_norm_type(f.get("type")),
            mode="inferred" if str(f.get("mode") or "") == "inferred" else "verbatim",
            required=bool(f.get("required")),
            enum_values=[str(v) for v in (f.get("enum_values") or [])],
            instruction=str(f.get("instruction") or f.get("label") or ""),
            columns=cols))
    return fields


# —— rule import channel 2: natural-language requirement -> field draft ——

_TEXT_DRAFT_PROMPT = (
    "你是文档抽取技能设计助手。用户会用一段自然语言描述想从某类文档中抽取的字段和规则，"
    "请把它转换为结构化字段定义草稿。\n"
    "输出 JSON：{\"doc_type\": \"文档类型\", \"fields\": [{\"name\": \"英文snake_case字段名\","
    " \"label\": \"中文名\", \"type\": \"string|number|date|enum|table\","
    " \"mode\": \"verbatim|inferred\", \"required\": true|false,"
    " \"enum_values\": [\"仅enum类型填\"],"
    " \"instruction\": \"抽取说明——尽量保留用户原话中的清洗/格式规则\","
    " \"columns\": [{\"name\": \"仅table类型填：列名\", \"instruction\": \"列说明\"}]}]}\n"
    "规则：用户提到的每个字段都要出现；用户没提但该类文档显然需要的关键字段可以补充（不超过 3 个）；"
    "需要判断/分类/推导的字段 mode=inferred；重复行明细归为一个 table 字段；只输出 JSON。"
)


def draft_from_text(text: str, provider: str | None = None, transport=None) -> dict:
    """Natural-language requirement -> drafted fields (editor prefill;
    user reviews and confirms before anything is saved)."""
    raw, usage, used = chat_json_with_fallback(
        [{"role": "system", "content": _TEXT_DRAFT_PROMPT},
         {"role": "user", "content": text[:8000]}],
        _studio_chain(provider), transport=transport)
    return {"draft": raw, "usage": usage, "provider_used": used}


# —— LLM instruction enrichment: terse notes -> production-grade rules ——

_ENRICH_PROMPT = (
    "你是文档抽取技能设计专家。给定一组字段（名称/类型/现有说明），为每个字段撰写生产级抽取说明，"
    "采用三段式：寻找关键词（含多语言常见标签）→ 清洗规则 → 输出格式。\n"
    "要求：保留并吸收现有说明里的语义（尤其是用户写明的格式/清洗要求，不得丢弃）；"
    "金额类统一去货币符号和千分位、输出 xxx.xx；日期类统一 YYYY-MM-DD；"
    "枚举类说明每个取值的判断依据；table 字段的每一列也要写说明。\n"
    "输出 JSON：{\"fields\": [{\"name\": \"与输入一致\", \"instruction\": \"完整抽取说明\","
    " \"columns\": [{\"name\": \"列名\", \"instruction\": \"列说明\"}]}]}。"
    "只输出 JSON，不要改动字段名，不要新增或删除字段。"
)


def enrich_fields(fields: list[dict], doc_type: str = "",
                  provider: str | None = None, transport=None) -> dict:
    """Expand field instructions into full extraction rules (keyword hunt ->
    cleaning -> output format). Prefill-only: caller merges, user confirms."""
    brief = [{"name": f.get("name"), "type": f.get("type"),
              "instruction": f.get("instruction") or "",
              "columns": [{"name": c.get("name"),
                           "instruction": c.get("instruction") or ""}
                          for c in (f.get("columns") or [])]}
             for f in fields if f.get("name")]
    user = json.dumps({"doc_type": doc_type, "fields": brief}, ensure_ascii=False)
    raw, usage, used = chat_json_with_fallback(
        [{"role": "system", "content": _ENRICH_PROMPT},
         {"role": "user", "content": user[:16000]}],
        _studio_chain(provider), transport=transport)
    return {"draft": raw, "usage": usage, "provider_used": used}


# —— rule import channel 3: spreadsheet/CSV -> field draft (no LLM) ——

_HEADER_ALIASES = {
    "name": {"字段名", "字段", "字段名称", "name", "field", "字段英文名", "英文名"},
    "label": {"中文名", "标题", "label", "显示名"},
    "type": {"类型", "type", "字段类型"},
    "instruction": {"说明", "描述", "规则", "抽取说明", "抽取规则", "说明/规则",
                    "instruction", "description"},
    "required": {"必填", "required", "是否必填"},
    "mode": {"模式", "mode", "抽取模式"},
    "enum": {"枚举", "枚举值", "enum", "选项"},
    "parent": {"所属明细表", "明细表", "所属表格", "父字段", "parent", "table"},
}
_TYPE_ALIASES = {
    "string": {"string", "文本", "字符串", "text", "str"},
    "number": {"number", "数字", "金额", "数值", "num", "int", "float"},
    "date": {"date", "日期", "时间", "datetime"},
    "enum": {"enum", "枚举"},
    "table": {"table", "明细", "明细表", "表格", "数组", "array", "list"},
}
_TRUTHY = {"是", "y", "yes", "true", "1", "√", "必填", "x"}


def _norm_type(v) -> str:
    s = str(v or "").strip().lower()
    for canonical, aliases in _TYPE_ALIASES.items():
        if s == canonical or s in aliases:
            return canonical
    return "string"


def _norm_header(h) -> str | None:
    s = str(h or "").strip().lower()
    for canonical, aliases in _HEADER_ALIASES.items():
        if s == canonical or s in aliases:
            return canonical
    return None


def fields_from_table(rows: list[list]) -> list[FieldSpec]:
    """Spreadsheet rows (header + data) -> FieldSpec list. Header names match
    loosely in Chinese or English; a 所属明细表 column nests its row as a
    column of that table field (created on demand)."""
    if not rows:
        return []
    header = [_norm_header(h) for h in rows[0]]
    if "name" not in header:
        raise ValueError("表头缺少字段名列（字段名/name）")

    def cell(row: list, key: str) -> str:
        try:
            i = header.index(key)
        except ValueError:
            return ""
        return str(row[i]).strip() if i < len(row) and row[i] is not None else ""

    fields: list[FieldSpec] = []
    by_name: dict[str, FieldSpec] = {}
    for row in rows[1:]:
        name = cell(row, "name")
        if not name:
            continue
        instruction = cell(row, "instruction") or cell(row, "label") or name
        spec = FieldSpec(
            name=name, type=_norm_type(cell(row, "type")),
            mode="inferred" if cell(row, "mode").lower() in ("inferred", "推断", "模型推断")
            else "verbatim",
            required=cell(row, "required").lower() in _TRUTHY,
            enum_values=[v.strip() for v in cell(row, "enum").replace("，", ",").split(",")
                         if v.strip()],
            instruction=instruction)
        parent = cell(row, "parent")
        if parent:
            tbl = by_name.get(parent)
            if tbl is None:
                tbl = FieldSpec(name=parent, type="table", instruction=parent)
                fields.append(tbl)
                by_name[parent] = tbl
            tbl.type = "table"
            tbl.columns.append(spec)
        else:
            fields.append(spec)
            by_name[name] = spec
    return fields
