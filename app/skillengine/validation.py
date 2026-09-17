"""SkillPackage v2 structural validation (9.15 §3.3) — PURE CORE.

No DB/framework imports: reference resolution is injected via `resolver`
(a callable returning the referenced skill's publish info, or None).
`validate_package` returns (errors, warnings) where each error carries a
`path` so the editor can pin it to a flow-rail node, e.g.
{"path": "categories[1].fields", "message": "..."}.

stage: "save" runs structural/limit rules; "publish" additionally enforces
the publish-only gates (inline fields, fast-mode constraints, references).
"""
import re

from app.skillengine.schema import FieldSpec, SkillPackage

_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")

MAX_CATEGORIES = 50
MAX_FIELDS_PER_GROUP = 200
MAX_FIELD_NAME = 100
MAX_DOC_TYPE = 64
MAX_RECOGNITION = 2000
MAX_RULES = 8000

_FORMAT_HINT = "字段名只能用字母数字下划线，不以 $ 开头"


def _check_field_group(fields: list[FieldSpec], path: str, errors: list[dict],
                       list_mode: bool = False) -> None:
    if len(fields) > MAX_FIELDS_PER_GROUP:
        errors.append({"path": path,
                       "message": f"字段数量超过上限（{len(fields)} > {MAX_FIELDS_PER_GROUP}）"})
    seen: set[str] = set()
    for i, f in enumerate(fields):
        fp = f"{path}[{i}]"
        name = (f.name or "").strip()
        if not name:
            errors.append({"path": f"{fp}.name", "message": "字段名不能为空"})
        elif len(name) > MAX_FIELD_NAME:
            errors.append({"path": f"{fp}.name",
                           "message": f"字段名超过 {MAX_FIELD_NAME} 字"})
        elif name.startswith("$"):
            errors.append({"path": f"{fp}.name", "message": _FORMAT_HINT})
        elif not re.match(r"^[\w\u4e00-\u9fff\-]+$", name):
            errors.append({"path": f"{fp}.name", "message": _FORMAT_HINT})
        if name in seen:
            errors.append({"path": f"{fp}.name", "message": f"同组内字段名重复：{name}"})
        seen.add(name)
        if f.type == "table":
            if list_mode:
                errors.append({"path": fp,
                               "message": "List 输出模式下字段不能是表格类型"})
            for j, col in enumerate(f.columns):
                if col.type == "table":
                    errors.append({"path": f"{fp}.columns[{j}]",
                                   "message": "表格列里不能再嵌套表格"})


def validate_package(pkg: SkillPackage, stage: str = "save",
                     resolver=None) -> tuple[list[dict], list[str]]:
    """Returns (errors, warnings). `resolver(skill_code)` returns
    {"version": int, "skill_mode": str} | None for existing_skill references."""
    errors: list[dict] = []
    warnings: list[str] = []

    # 9.15 WP5: fast mode executes as standard extraction — no categories,
    # no classification (editor greys the cards; this is the API-side gate)
    if pkg.processing_mode == "fast" and pkg.skill_mode == "advanced":
        errors.append({"path": "processing_mode",
                       "code": "fast_mode_advanced_conflict",
                       "message": "极速模式不支持高级提取，请先切换为标准提取"})

    # skill code shape — enforced for new (v2) packages only; legacy v1 codes
    # keep loading untouched
    if pkg.schema_version >= 2 and not _CODE_RE.match(pkg.skill_code or ""):
        errors.append({"path": "skill_code",
                       "message": "技能代码需以字母开头，只能含字母数字、下划线和中划线"})

    if pkg.schema_version >= 2 or pkg.skill_mode == "advanced" \
            or pkg.output_shape == "list" or pkg.output.enabled:
        # —— v2 field groups ——
        _check_field_group(pkg.fields, "fields", errors,
                           list_mode=(pkg.output_shape == "list"))
        if len((pkg.additional_rules or "")) > MAX_RULES:
            errors.append({"path": "additional_rules",
                           "message": f"附加规则超过 {MAX_RULES} 字"})

    if pkg.skill_mode != "advanced":
        if pkg.processing_mode == "fast" and stage == "publish":
            if not pkg.fields:
                errors.append({"path": "fields",
                               "code": "fast_mode_requires_standard_fields",
                               "message": "极速模式需要先配置标准字段"})
            for f in pkg.fields:
                if f.entity_list:
                    errors.append({"path": "fields",
                                   "message": "极速模式不支持脱敏清单（entity_list）字段："
                                              "极速没有定位能力"})
        if pkg.review_policy.mode == "never" and pkg.kind == "audit":
            warnings.append("脱敏类技能建议保留人工复核")
        return errors, warnings

    # —— advanced mode (§3.3) ——
    cats = pkg.categories
    if len(cats) > MAX_CATEGORIES:
        errors.append({"path": "categories",
                       "message": f"类别数量超过上限（{len(cats)} > {MAX_CATEGORIES}）"})
    others = [c for c in cats if c.is_other]
    if len(others) != 1:
        errors.append({"path": "categories",
                       "message": "高级模式必须恰好有一个「Other（未分类）」类别"})
    else:
        other = others[0]
        if other.doc_type and other.doc_type != "Other":
            errors.append({"path": f"categories[{cats.index(other)}].doc_type",
                           "message": "Other 类别的 doc_type 固定为 Other，不能改名"})
    seen_types: set[str] = set()
    for i, c in enumerate(cats):
        cp = f"categories[{i}]"
        if len(c.doc_type) > MAX_DOC_TYPE:
            errors.append({"path": f"{cp}.doc_type",
                           "message": f"doc_type 超过 {MAX_DOC_TYPE} 字"})
        if not c.is_other:
            if c.doc_type in seen_types:
                errors.append({"path": f"{cp}.doc_type",
                               "message": f"普通类别的 doc_type 必须唯一：{c.doc_type}"})
            seen_types.add(c.doc_type)
        if len(c.recognition_instruction) > MAX_RECOGNITION:
            errors.append({"path": f"{cp}.recognition_instruction",
                           "message": f"识别说明超过 {MAX_RECOGNITION} 字"})
        if len(c.additional_rules) > MAX_RULES:
            errors.append({"path": f"{cp}.additional_rules",
                           "message": f"附加规则超过 {MAX_RULES} 字"})
        _check_field_group(c.fields, f"{cp}.fields", errors,
                           list_mode=(c.output_shape == "list"))
        if c.handler == "existing_skill":
            if not c.skill_ref or not c.skill_ref.skill_code:
                errors.append({"path": f"{cp}.skill_ref",
                               "message": "「使用已有技能」必须选择被引用的技能"})
            elif stage == "publish":
                info = resolver(c.skill_ref.skill_code) if resolver else None
                if info is None:
                    errors.append({"path": f"{cp}.skill_ref",
                                   "code": "reference_unavailable",
                                   "message": f"引用的技能 {c.skill_ref.skill_code} "
                                              "不存在、无发布版或不可引用"})
                elif info.get("skill_mode") == "advanced":
                    errors.append({"path": f"{cp}.skill_ref",
                                   "code": "reference_unavailable",
                                   "message": "只能引用标准模式的技能"})
        if stage == "publish" and c.handler == "inline" and not c.fields:
            errors.append({"path": f"{cp}.fields",
                           "message": "「在此定义字段」的类别发布前至少要有一个字段"})

    if stage == "publish":
        warnings.append("高级提取运行时在 WP4 上线；当前版本发布的高级技能将在运行时就绪后生效")
    return errors, warnings