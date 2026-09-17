"""9.15 WP6 (R12): output file naming — the ONLY naming implementation
(server renders previews too; the frontend never reimplements it). Pure
module: no DB/framework imports (architecture test enforces this).

Pattern grammar (§WP6):
- {original_name} {original_ext} {date} {time} {doc_type} {doc_index}
  {data.<field>}; unknown variables are a VALIDATION error, never a runtime
  surprise; `{{`/`}}` render literal braces.
- {original_ext} is the OUTPUT extension: `.pdf` when the artifact is a PDF
  (searchable PDF on, or a split slice), else the original suffix lowercased.
- If the rule omits {original_ext} it is appended automatically (preview
  shows it) so artifacts never lose their extension.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_RENAME = "{original_name}{original_ext}"
DEFAULT_SPLIT = "{doc_index}_{doc_type}_{original_name}{original_ext}"

FIXED = ("original_name", "original_ext", "date", "time", "doc_type", "doc_index")
_TOKEN_RE = re.compile(r"\{\{|\}\}|\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\}")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_MAX_BYTES = 180


def validate_pattern(pattern: str) -> list[dict]:
    """Unknown {vars} -> structured errors (save/preview time, §WP6)."""
    errors: list[dict] = []
    for m in _TOKEN_RE.finditer(pattern or ""):
        name = m.group(1)
        if name is None:
            continue
        if name in FIXED:
            continue
        if name.startswith("data.") and len(name) > 5:
            continue
        errors.append({"path": "naming_rule", "token": f"{{{name}}}",
                       "message": f"未知变量 {{{name}}}"})
    return errors


def tokens_of(pattern: str) -> list[str]:
    """Distinct variables for the editor's clickable tag list."""
    out: list[str] = []
    for m in _TOKEN_RE.finditer(pattern or ""):
        if m.group(1):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def ensure_extension(pattern: str) -> tuple[str, bool]:
    """Append {original_ext} when missing -> (normalized, appended)."""
    p = pattern or ""
    if "{original_ext}" in p or "{data." in p:
        # {data.*} may hold a field the user renames with — still require the
        # explicit extension token; missing it is the classic no-extension bug
        pass
    if "{original_ext}" in p:
        return p, False
    return p + "{original_ext}", True


def sanitize(name: str) -> str:
    """Windows-safe filename cleaning (§WP6): NFC, strip control/reserved
    chars, merge whitespace, trailing dots/spaces, reserved names prefixed,
    ≤180 UTF-8 bytes without splitting a character, empty -> fallback."""
    s = unicodedata.normalize("NFC", name or "")
    s = "".join(ch for ch in s if ord(ch) >= 32 and ch not in '\\/:*?"<>|')
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")
    main, dot, ext = s.rpartition(".")
    if not dot:
        main, ext = s, ""
    if main.upper() in _RESERVED:
        main = f"_{main}"
    if len((main + dot + ext).encode("utf-8")) > _MAX_BYTES:
        budget = _MAX_BYTES - len(ext.encode("utf-8")) - len(dot.encode("utf-8"))
        trimmed = ""
        for ch in main:
            if len((trimmed + ch).encode("utf-8")) > budget:
                break
            trimmed += ch
        main = trimmed
    if not main:
        main = ""            # caller supplies document_{doc_index} fallback
    return main + (dot + ext if dot else "")


def render(pattern: str, *, original_name: str, original_ext: str,
           output_is_pdf: bool, doc_type: str | None = None,
           doc_index: int | None = None, data: dict | None = None,
           completed_at: datetime | None = None,
           output_tz: str = "Asia/Shanghai") -> tuple[str, list[dict]]:
    """Render the display filename. Returns (filename, errors); errors only
    from unknown variables — rendering never invents values silently."""
    errors = validate_pattern(pattern)
    if errors:
        return "", errors
    p, _appended = ensure_extension(pattern)
    tz = ZoneInfo(output_tz)
    now = completed_at or datetime.now(timezone.utc)
    local = now.astimezone(tz)

    ext = ".pdf" if output_is_pdf else (original_ext or "").lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    stem = original_name.rsplit(".", 1)[0] if original_name else ""

    def _scalar(field: str) -> str:
        v = (data or {}).get(field)
        if isinstance(v, (dict, list)):
            return ""                     # tables/complex values are not nameable
        return "" if v is None else str(v)

    def _sub(m: re.Match) -> str:
        if m.group(0) == "{{":
            return "{"
        if m.group(0) == "}}":
            return "}"
        name = m.group(1)
        if name == "original_name":
            return stem
        if name == "original_ext":
            return ext
        if name == "date":
            return local.strftime("%Y%m%d")
        if name == "time":
            return local.strftime("%H%M%S")
        if name == "doc_type":
            return doc_type or ""
        if name == "doc_index":
            return f"{doc_index:03d}" if doc_index and doc_index >= 100 \
                else f"{doc_index:02d}" if doc_index else ""
        if name.startswith("data."):
            return _scalar(name[5:])
        return m.group(0)

    raw = _TOKEN_RE.sub(_sub, p)
    base = sanitize(raw)
    main_part = base.rpartition(".")[0] if "." in base else base
    if not main_part:
        # only an extension survived (e.g. the data fields were empty):
        # fall back to document_{n}, keeping the produced extension
        base = sanitize(f"document_{doc_index or 1}")
        if ext and not base.endswith(ext):
            base += ext
    return base, []
