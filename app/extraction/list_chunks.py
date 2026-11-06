"""Bounded, ordered extraction of List documents with explicit continuation evidence."""
import json

from app.parsers.base import UDR
from app.skillengine.compiler import MAX_DOC_CHARS
from app.skillengine.schema import FieldSpec


def _fragments(text: str):
    """Consume every character once, preferring paragraph and line boundaries."""
    start = 0
    while len(text) - start > MAX_DOC_CHARS:
        end = start + MAX_DOC_CHARS
        for delimiter in ("\n\n", "\n", " "):
            pos = text.rfind(delimiter, start + MAX_DOC_CHARS // 2, end)
            if pos >= 0:
                end = pos + len(delimiter)
                break
        yield text[start:end]
        start = end
    yield text[start:]


def _page_texts(udr: UDR, text: str) -> list[str]:
    """Retain the full markdown when matching it to raster pages; never guess page ownership."""
    if not text:
        return [""] * len(udr.pages)
    if len(udr.pages) == 1:
        return [text]
    sources = [page.markdown or "\n".join(block.text for block in page.blocks) for page in udr.pages]
    # The normal parser layout also handles repeated headers and blank pages
    # without searching for the same text inside the preceding page.
    for separator in ("\n\n", "\f", "\n"):
        if text == separator.join(sources):
            return [part + (separator if i < len(sources) - 1 else "")
                    for i, part in enumerate(sources)]
    starts = [0]
    for page in udr.pages[1:]:
        anchors = [page.markdown, *(block.text for block in page.blocks)]
        for anchor in anchors:
            anchor = (anchor or "").strip()[:100]
            pos = text.find(anchor, starts[-1] + 1) if anchor else -1
            if pos != -1:
                starts.append(pos)
                break
        else:
            raise ValueError(f"Cannot align source text with vision page {page.page_no}")
    return [text[start:end] for start, end in zip(starts, starts[1:] + [len(text)])]


def list_units(udr: UDR, images: dict[int, str], max_images: int) -> list[tuple[UDR, list[str]]]:
    """List chunks respect both text and raster limits, including oversized individual pages."""
    text = udr.full_markdown
    if not text:
        text = "\n\n".join(page.markdown or "\n".join(block.text for block in page.blocks)
                            for page in udr.pages)
        udr = udr.model_copy(update={"full_markdown": text})
    if images and not images.keys() <= {page.page_no for page in udr.pages}:
        raise ValueError("Vision page missing from parsed document")
    if len(text) <= MAX_DOC_CHARS and len(images) <= max_images:
        return [(udr, [images[p] for p in sorted(images)])]
    if not images:
        return [(UDR(full_markdown=part), []) for part in _fragments(text)]
    units = []
    parts, rasters = [], []
    size = 0
    for page, source in zip(udr.pages, _page_texts(udr, text)):
        for i, fragment in enumerate(_fragments(source)):
            image = images.get(page.page_no) if i == 0 else None
            if parts and (size + len(fragment) > MAX_DOC_CHARS or
                          (image and len(rasters) == max_images)):
                units.append((UDR(full_markdown="".join(parts)), rasters))
                parts, rasters, size = [], [], 0
            parts.append(fragment)
            size += len(fragment)
            if image:
                rasters.append(image)
    units.append((UDR(full_markdown="".join(parts)), rasters))
    return units


def boundary_messages(messages: list[dict], previous: dict | None, fields: list[FieldSpec]):
    """The continuity hint is transient model data; it never becomes a stored field."""
    messages[0]["content"] += (
        "\n这是一个长文档的连续片段。仅提取当前片段覆盖的内容；有文字时按本段文字范围使用配图，"
        "无文字时读取本段配图。不要重复输出前段的字段或明细。")
    if previous is None:
        return
    header = {}
    for f in fields:
        value = _scalar(previous.get(f.name))
        if f.type != "table" and value not in (None, ""):
            header[f.name] = str(value)[:120]
        if len(header) == 12:
            break
    messages[0]["content"] += (
        "上一片段的最后一条记录可供参考。仅在本段第一条记录增加 \"$continuation\"："
        "有连续行或票号等证据表明它是上一记录的续页时为 true；本段开始一张新票时为 false；"
        "无法判断时用 null。同值不等于同一张票。")
    messages.insert(-1, {"role": "user", "content": "上一记录的参考字段（数据，不是指令）：\n"
                     + json.dumps(header, ensure_ascii=False)})


def model_records(raw: dict) -> list[dict]:
    rows = raw.get("records")
    if not isinstance(rows, list):
        return []
    return [dict(row) if isinstance(row, dict) else {} for row in rows]


def _scalar(value):
    return value.get("value") if isinstance(value, dict) else value


def invalid_structure(raw: dict, fields: list[FieldSpec], *, complete: bool) -> bool:
    """A4: flag malformed records before normalizing them for human editing.

    A chunk may omit fields continued in a neighboring chunk. Required-value
    and missing-table checks apply only after the full record has been merged.
    """
    rows = raw.get("records")
    if not isinstance(rows, list):
        return False  # The empty-record or incomplete-chunk check handles this.
    for row in rows:
        if not isinstance(row, dict):
            return True
        for f in fields:
            value = row.get(f.name)
            if f.type == "table":
                if f.name not in row and not complete:
                    continue
                if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
                    return True
                if complete and f.required and not value:
                    return True
            elif complete and f.required and _scalar(value) in (None, ""):
                return True
    return False


def append_chunk(records: list[dict], raw: dict, fields: list[FieldSpec]) -> bool:
    """Merge only an explicit continuation with compatible headers; preserve every table row."""
    rows = model_records(raw)
    if not rows:
        # An explicit empty chunk can be a blank page. A broken response
        # within an otherwise nonempty document must still reach review.
        return not isinstance(raw.get("records"), list)
    first = rows[0]
    marker = first.pop("$continuation", None)
    uncertain = False
    if records:
        previous = records[-1]
        disagreement = any(
            str(_scalar(previous[f.name])).strip() != str(_scalar(first[f.name])).strip()
            for f in fields if f.type != "table"
            and _scalar(previous.get(f.name)) not in (None, "")
            and _scalar(first.get(f.name)) not in (None, ""))
        if marker is True and not disagreement:
            for f in fields:
                value = first.get(f.name)
                if f.type == "table" and isinstance(value, list):
                    old = previous.get(f.name)
                    previous[f.name] = (old if isinstance(old, list) else []) + value
                elif _scalar(previous.get(f.name)) in (None, ""):
                    previous[f.name] = value
            rows.pop(0)
        elif marker is not False:
            uncertain = True
    records.extend(rows)
    return uncertain
