"""9.15 WP4 (§3.5): clean document view projected from FileRecord.document_meta.

`source_pages` is the fact source (original-file page numbers, possibly
non-consecutive); `page_range` is a display-only derivation — non-consecutive
pages render as "2,4", never the lying interval "2-4". Legacy files without
document_meta project `None` (new keys only — §3.6 backwards compat).
"""
from __future__ import annotations


def page_range(pages: list[int]) -> str:
    if not pages:
        return ""
    parts: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def document_view(f) -> dict | None:
    """The `document` object for /status children and /review child payloads."""
    meta = f.document_meta or {}
    if meta.get("doc_index") is None:
        return None
    source = list(meta.get("source_pages") or [])
    status = meta.get("extraction_status")
    if status is None:
        status = "completed" if f.status in ("completed", "passed") else f.status
    schema = meta.get("effective_schema") or {}
    return {
        "doc_index": meta["doc_index"],
        "doc_type": meta.get("doc_type"),
        "category_id": meta.get("category_id"),
        "handler": meta.get("handler"),
        "source_pages": source,
        "page_range": page_range(source),
        "extraction_status": status,
        "effective_fields": [fl.get("name") for fl in schema.get("fields", [])
                             if isinstance(fl, dict) and fl.get("name")],
    }
