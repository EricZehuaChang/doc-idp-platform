"""9.15 WP4 (R07/图04): document classifier for advanced-mode skills.

Turns one parsed file plus the skill's category list into a DocumentPlan —
which pages belong to which document and under which category — using one LLM
call per layout (windowed when the file exceeds 40 pages). The v1/standard
auto-split (`splitter.classify_pages`) is untouched and keeps serving those
modes (protected asset 3).

Output contract (validated before use — §WP4 安全门禁):
    plan = [{"pages": [1, 2], "category_id": "invoice"}, ...]
Pages are the ORIGINAL file's page numbers, ascending, disjoint, and together
they cover every page. Unknown category ids fall back to the Other category.
A plan that fails validation twice (once with a stricter retry prompt) raises
ClassificationError — the caller treats the whole file as failed
(`classification_failed`), never silently shoving pages into Other.
"""
from __future__ import annotations

import logging
from typing import Any

from app.extraction.provider_client import chat_json_with_fallback
from app.parsers.base import UDR

log = logging.getLogger("idp.classifier")

WINDOW_PAGES = 40          # per-call page cap; adjacent windows overlap 1 page
SINGLE_LOOK_PAGES = 5      # `single` layout only reads the first pages
EXCERPT_CHARS = 600        # per-page excerpt cap (安全门禁: 摘录长度上限)


class ClassificationError(RuntimeError):
    """The classifier could not produce a valid plan after the retry."""


# ———————————————————————————————————————— prompts —————————————————————————
_SYSTEM = (
    "你是文档分类助手。给定一个文件的逐页文字摘录和候选类别列表，"
    "判断文件构成并把每一页归入一个类别。"
    '只输出 JSON，不要输出其它文字。')

_PROMPTS = {
    # one document in the file: which category is it?
    "single": (
        "该文件是一份完整文档（可能是多种页面组成的同一份单据，如发票+附件）。"
        "根据前几页判断它的类别。\n候选类别：\n{categories}\n"
        "逐页摘录：\n{excerpts}\n"
        '输出：{{"category_id": "<类别id>"}}。若都不匹配，输出 Other 类的 id。'),
    # every page is its own document, all the same category
    "same_type_independent": (
        "该文件的每一页都是一份独立文档（如逐页一联的送货单），且类别相同。"
        "判断这个共同类别。\n候选类别：\n{categories}\n逐页摘录：\n{excerpts}\n"
        '输出：{{"category_id": "<类别id>"}}。若都不匹配，输出 Other 类的 id。'),
    # pages of the same category, but a document may span pages
    "same_type_continuous": (
        "该文件的页面属于同一类单据，但一份文档可能连续占用多页。"
        "先判断类别，再逐页判断该页是否是【一份新文档的第一页】。"
        "依据：新的单据号/抬头/日期=新文档；续行、合计、条款延续=续页。\n"
        "候选类别：\n{categories}\n逐页摘录：\n{excerpts}\n"
        '输出：{{"category_id": "<类别id>", "pages": '
        '[{{"page": 页码, "new_doc": true|false}}, ...]}}，覆盖每一页，'
        "第 1 页恒为 new_doc=true。"),
    # the file mixes different document types
    "mixed": (
        "该文件混杂多种类型的单据。逐页判断该页属于哪个类别、"
        "以及该页是否是【一份新文档的第一页】"
        "（同类别的连续页也可能开起新文档）。\n"
        "候选类别：\n{categories}\n逐页摘录：\n{excerpts}\n"
        '输出：{{"pages": [{{"page": 页码, "category_id": "<类别id>", '
        '"new_doc": true|false}}, ...]}}，覆盖每一页，第 1 页恒为 new_doc=true。'),
}

_RETRY_NOTE = (
    "\n\n【重试要求】上一次输出不合格。严格遵守：只输出 JSON；"
    "逐页输出必须覆盖输入中的每一页且每页恰好一次，不得遗漏或重复；"
    "类别id必须取自候选列表；第 1 页 new_doc 恒为 true。")


def _excerpt(page) -> str:
    text = page.markdown or " ".join(b.text for b in page.blocks if b.text)
    return " ".join(text.split())[:EXCERPT_CHARS]


def _category_lines(categories: list[dict[str, Any]]) -> str:
    lines = []
    for c in categories:
        doc_type = c.get("doc_type") or c["id"]
        line = f'- id={c["id"]}（{doc_type}）'
        if c.get("recognition_instruction"):
            line += f"：{c['recognition_instruction'][:200]}"
        if c.get("is_other"):
            line += "【兜底类别 Other】"
        lines.append(line)
    return "\n".join(lines)


def _windows(total: int) -> list[list[int]]:
    """Page windows of ≤WINDOW_PAGES, adjacent windows overlap 1 page."""
    if total <= WINDOW_PAGES:
        return [list(range(1, total + 1))]
    out, start = [], 1
    while start <= total:
        end = min(start + WINDOW_PAGES - 1, total)
        out.append(list(range(start, end + 1)))
        if end >= total:
            break
        start = end                     # overlap: next window re-reads `end`
    return out


def _other_id(categories: list[dict[str, Any]]) -> str | None:
    for c in categories:
        if c.get("is_other"):
            return c["id"]
    return None


def _normalize_pages(raw: Any, page_nos: list[int],
                     categories: list[dict[str, Any]]) -> dict[int, dict]:
    """Validate per-page output: every page exactly once, category known
    (unknown -> Other). Raises ValueError on structural violations."""
    other = _other_id(categories)
    known = {c["id"] for c in categories}
    entries = raw.get("pages") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError("missing pages list")
    seen: dict[int, dict] = {}
    for e in entries:
        if not isinstance(e, dict) or "page" not in e:
            raise ValueError("malformed page entry")
        p = int(e["page"])
        if p in seen:
            raise ValueError(f"page {p} appears more than once")
        cat = e.get("category_id")
        if cat not in known:
            cat = other
        if cat is None:
            raise ValueError(f"page {p}: unknown category and no Other fallback")
        seen[p] = {"category_id": cat, "new_doc": bool(e.get("new_doc"))}
    missing = [p for p in page_nos if p not in seen]
    if missing:
        raise ValueError(f"pages not covered: {missing[:5]}")
    return seen


def _merge_page_marks(page_nos: list[int],
                      marks: dict[int, dict]) -> list[dict]:
    """Group per-page marks into documents: a new document starts on
    new_doc=true pages; consecutive same-category continuation pages join.
    Page 1 must start a document (guaranteed by caller validation)."""
    plan: list[dict] = []
    for i, p in enumerate(page_nos):
        m = marks[p]
        if i == 0 or m["new_doc"] or not plan:
            plan.append({"pages": [p], "category_id": m["category_id"]})
        else:
            plan[-1]["pages"].append(p)
            # a continuation page with a different category than its document:
            # trust the document's original category for coherence — the model
            # said new_doc=false, so it is still the same document
    return plan


def plan_documents(udr: UDR, categories: list[dict[str, Any]],
                   document_layout: str, additional_rules: str = "",
                   provider: str | None = None, transport=None,
                   ) -> tuple[list[dict], dict]:
    """Classify one parsed file. Returns (plan, usage) where plan is a list of
    {"pages": [...], "category_id": ...} in document order (doc_index is the
    list position). Raises ClassificationError when no valid plan is produced."""
    page_nos = [p.page_no for p in udr.pages]
    if not page_nos:
        raise ClassificationError("empty document: nothing to classify")
    if not categories:
        raise ClassificationError("advanced skill has no categories")
    cat_lines = _category_lines(categories)
    rules = (f"\n分类附加规则：{additional_rules[:2000]}" if additional_rules else "")

    def call(prompt: str) -> tuple[dict, dict, str]:
        messages = [{"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt}]
        raw, usage, used = chat_json_with_fallback(
            messages, [provider], transport=transport)
        usage = dict(usage or {})
        usage["provider_used"] = used
        return raw, usage, used

    def excerpts(pages: list[int], cap: int | None = None) -> str:
        wanted = pages[:cap] if cap else pages
        return "\n".join(
            f"第{p}页开头: {_excerpt(udr.pages[p - 1])}" for p in wanted)

    if document_layout == "single":
        prompt = _PROMPTS["single"].format(
            categories=cat_lines, excerpts=excerpts(page_nos, SINGLE_LOOK_PAGES))
        for attempt, note in enumerate(("", _RETRY_NOTE)):
            try:
                raw, usage, _ = call(prompt + rules + note)
                cat = raw.get("category_id")
                if cat not in {c["id"] for c in categories}:
                    cat = _other_id(categories)
                if cat is None:
                    raise ValueError("unknown category and no Other fallback")
                return [{"pages": list(page_nos), "category_id": cat}], usage
            except ClassificationError:
                raise
            except Exception as e:
                log.warning("single classify attempt %s failed: %s", attempt + 1, e)
        raise ClassificationError("single-layout classification failed twice")

    if document_layout == "same_type_independent":
        prompt = _PROMPTS["same_type_independent"].format(
            categories=cat_lines, excerpts=excerpts(page_nos))
        for attempt, note in enumerate(("", _RETRY_NOTE)):
            try:
                raw, usage, _ = call(prompt + rules + note)
                cat = raw.get("category_id")
                if cat not in {c["id"] for c in categories}:
                    cat = _other_id(categories)
                if cat is None:
                    raise ValueError("unknown category and no Other fallback")
                return [{"pages": [p], "category_id": cat} for p in page_nos], usage
            except Exception as e:
                log.warning("independent classify attempt %s failed: %s", attempt + 1, e)
        raise ClassificationError("independent-layout classification failed twice")

    # per-page layouts — windowed above 40 pages, later window wins overlaps
    per_page = document_layout in ("mixed", "same_type_continuous")
    if not per_page:
        raise ClassificationError(f"unknown document_layout: {document_layout}")
    marks: dict[int, dict] = {}
    usage: dict = {}
    for win in _windows(len(page_nos)):
        template = _PROMPTS[document_layout]
        prompt = template.format(categories=cat_lines, excerpts=excerpts(win))
        win_marks: dict[int, dict] | None = None
        for attempt, note in enumerate(("", _RETRY_NOTE)):
            try:
                raw, u, _ = call(prompt + rules + note)
                usage = u or usage
                win_marks = _normalize_pages(raw, win, categories)
                break
            except Exception as e:
                log.warning("%s classify window %s attempt %s failed: %s",
                            document_layout, win[0], attempt + 1, e)
        if win_marks is None:
            raise ClassificationError(
                f"{document_layout} classification failed for pages {win[0]}-{win[-1]}")
        marks.update(win_marks)          # overlap: the later window wins

    if page_nos[0] in marks and not marks[page_nos[0]]["new_doc"]:
        marks[page_nos[0]]["new_doc"] = True   # invariant: page 1 starts a doc

    plan = _merge_page_marks(page_nos, marks)
    return plan, usage
