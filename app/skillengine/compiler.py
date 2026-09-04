"""Prompt compiler (design v0.2 §5.1 core): SkillPackage -> messages for an
OpenAI-compatible chat call. Four-fold information injection: field-level
instructions + anchor hints + few-shot pairs + strict JSON output contract.
"""
import json

from app.parsers.base import UDR
from app.skillengine.schema import FieldSpec, SkillPackage

_MAX_DOC_CHARS = 24000   # final guard after multi-page detail-table map/reduce


def _rule_text(instruction: str, indent: str) -> str:
    """Render a field's rule without destroying its structure.

    Authors write multi-step rules across several lines; concatenating them into
    one run-on line inside a bullet lost the step boundaries the model is meant
    to follow. Continuation lines are re-indented under the bullet instead.
    """
    lines = [ln.strip() for ln in (instruction or "").strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return f" 规则: {lines[0]}"
    body = ("\n" + indent + "  ").join(lines)
    return f" 规则:\n{indent}  {body}"


def _mode_note(f: FieldSpec) -> str:
    return ("（推断字段：允许推理，必须给 reasoning）" if f.mode == "inferred"
            else "（原文字段：值必须来自文档原文，绝不编造）")


def _spec_line(f: FieldSpec, bullet: str, indent: str) -> str:
    """One field/column definition line. Columns render through the same path as
    scalars so a column never silently carries less than the author wrote."""
    rule = _rule_text(f.instruction, indent)
    anchors = f" 锚点词: {', '.join(f.anchor_hints)}" if f.anchor_hints else ""
    required = " 必填" if f.required else ""
    if f.type == "enum":
        head = f"{f.name}: 枚举，只能取 {f.enum_values}。"
    else:
        head = f"{f.name}: {f.type}。"
    return f"{indent}{bullet} {head}{_mode_note(f)}{required}{rule}{anchors}"


def _field_lines(fields: list[FieldSpec]) -> str:
    lines = []
    for f in fields:
        if f.type == "table":
            rule = _rule_text(f.instruction, "")
            sweep = ("（实体清单表：逐段扫描全文，找出所有命中值，每个值单独一行，"
                     "禁止遗漏；值必须逐字来自文档原文，同一值出现多处只输出一行）"
                     if f.entity_list else "")
            cols = ", ".join(c.name for c in f.columns)
            lines.append(f"- {f.name}: 明细表，输出对象数组，列: [{cols}]。{sweep}{rule}")
            # column rules used to be dropped entirely — only the names above
            # reached the model, so any cleaning/format rule an author wrote on a
            # line-item column was never sent at all
            if f.columns:
                lines.append("  每行对象的列定义：")
                lines.extend(_spec_line(c, "·", "  ") for c in f.columns)
        else:
            lines.append(_spec_line(f, "-", ""))
    return "\n".join(lines)


def _output_contract(fields: list[FieldSpec]) -> dict:
    """Example-shaped contract embedded in the prompt. Verbatim scalar fields ->
    string value; inferred -> {value, reasoning}; table -> array of row objects."""
    out: dict = {}
    for f in fields:
        if f.type == "table":
            out[f.name] = [{c.name: "..." for c in f.columns}]
        elif f.mode == "inferred":
            out[f.name] = {"value": "...", "reasoning": "..."}
        else:
            out[f.name] = "..."
    return out


def compile_messages(pkg: SkillPackage, udr: UDR,
                     images: list[str] | None = None) -> list[dict]:
    system = pkg.system_prompt or (
        f"你是文档结构化抽取引擎。文档类型：{pkg.doc_type_hint or '未知'}。\n"
        "任务：从给定文档内容中抽取字段，严格按输出契约返回 JSON。\n"
        "纪律：原文字段找不到就输出空字符串，绝不编造；金额/日期保持原文写法；"
        "只输出 JSON，无任何解释。"
    )
    if pkg.additional_rules:
        system += f"\n附加规则：{pkg.additional_rules}"
    # Field definitions belong to the skill contract, not to the document, and
    # they carry the author's per-field rules. Left in the user turn they were
    # one bullet in a long list ahead of up to 24k chars of document text, which
    # is why a rule written on a field lost to the same rule written as an
    # additional rule. They ride in the system turn with the other constraints.
    system += f"\n\n## 字段定义\n{_field_lines(pkg.fields)}"

    doc_text = udr.full_markdown or udr.full_text()
    if len(doc_text) > _MAX_DOC_CHARS:
        doc_text = doc_text[:_MAX_DOC_CHARS] + "\n...[截断]"

    user = (
        f"## 输出契约（JSON，键必须完全一致）\n"
        f"{json.dumps(_output_contract(pkg.fields), ensure_ascii=False)}\n\n"
        f"## 文档内容\n{doc_text}"
    )

    messages: list[dict] = [{"role": "system", "content": system}]
    for shot in pkg.few_shot:   # samples become few-shot pairs — the "model"
        messages.append({"role": "user", "content": shot.input_excerpt})
        messages.append({"role": "assistant",
                         "content": json.dumps(shot.expected_output, ensure_ascii=False)})
    if images:
        # Vision channel: the parsed text stays in the prompt (it carries the
        # anchors the confidence scorer later matches against) and the page
        # raster is added beside it, so the model can read what the parser
        # mangled — tables, stamps, handwriting. OpenAI-compatible content
        # parts; vendors that ignore image parts still see the text.
        messages.append({"role": "user", "content": [
            {"type": "text", "text": user},
            *({"type": "image_url", "image_url": {"url": u}} for u in images),
        ]})
    else:
        messages.append({"role": "user", "content": user})
    return messages
