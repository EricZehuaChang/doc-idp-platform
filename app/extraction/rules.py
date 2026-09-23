"""Deterministic extraction from parser output; misses never invent values.

User-authored regexes run in a bounded subprocess: catastrophic backtracking
cannot pin a worker thread. A timeout is a rule miss, following on_miss policy.
"""
import json
import re
import subprocess
import sys
from app.parsers.base import UDR
from app.skillengine.schema import FieldSpec

_REGEX_PROGRAM = """import json,re,sys
pattern,text=json.load(sys.stdin)
m=re.search(pattern,text,re.M)
print(json.dumps(m.group(1) if m else None))
"""


def _clean(text: str) -> str:
    return text.strip().strip("* ").strip()


def _cells(line: str) -> list[str]:
    return [_clean(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _scope(text: str, labels: list[str]) -> str:
    if not labels:
        return text
    lines = text.splitlines()
    return "\n".join("\n".join(lines[i:i + 3]) for i, line in enumerate(lines)
                      if any(label and label in line for label in labels))


def _anchor(text: str, labels: list[str]) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for label in labels:
            if not label:
                continue
            # Markdown key/value cells (Word / Excel parser output).
            cells = _cells(line) if "|" in line else []
            for j, cell in enumerate(cells[:-1]):
                if cell.rstrip(":： ") == label.rstrip(":： "):
                    value = cells[j + 1]
                    if value and not re.fullmatch(r"[-: ]+", value):
                        return value
            match = re.search(re.escape(label.rstrip(":： ")) + r"\s*[:：]\s*(.+)", line)
            if match:
                return _clean(match.group(1)) or None
            if _clean(line).rstrip(":： ") == label.rstrip(":： ") and i + 1 < len(lines):
                value = _clean(lines[i + 1])
                if value and not value.startswith("|"):
                    return value
    return None


def _table(text: str, field: FieldSpec) -> list[dict] | None:
    rule = field.rule
    lines = text.splitlines()
    selected = not rule.sheet_name
    indices = None
    rows = []
    for line in lines:
        if line.startswith("#"):
            if rule.sheet_name:
                selected = line.lstrip("# ").strip() == rule.sheet_name
            indices = None
            continue
        if not selected or "|" not in line:
            indices = None
            continue
        cells = _cells(line)
        if all(re.fullmatch(r"[-: ]*", c) for c in cells):
            continue
        mapping = {}
        for col in field.columns:
            aliases = rule.column_aliases.get(col.name) or [col.name]
            found = [i for i, c in enumerate(cells) if c.casefold() in {a.casefold() for a in aliases}]
            if len(found) == 1:
                mapping[col.name] = found[0]
        if len(mapping) == len(field.columns) and mapping:
            indices = mapping
            continue
        if indices is None:
            continue
        if any(c.casefold() == word.casefold() for c in cells for word in rule.stop_words):
            indices = None
            continue
        row = {name: cells[i] if i < len(cells) else "" for name, i in indices.items()}
        if any(row.values()):
            # A partial row is a miss: let the model complete the table rather
            # than silently accepting truncated columns.
            if any(not row.get(col.name) for col in field.columns):
                return None
            rows.append(row)
    return rows or None


def extract_rules(udr: UDR, fields: list[FieldSpec]) -> dict:
    text = udr.full_markdown or udr.full_text()
    values = {}
    for field in fields:
        rule = field.rule
        if not rule or field.mode == "inferred" or not text.strip():
            continue
        if rule.kind == "table" and field.type == "table":
            value = _table(text, field)
        elif rule.kind == "anchor" and field.type != "table":
            value = _anchor(text, rule.labels)
        elif rule.kind == "regex" and field.type != "table":
            scoped = _scope(text, rule.labels)
            if len(scoped) > 2_000_000:
                continue
            try:
                proc = subprocess.run([sys.executable, "-c", _REGEX_PROGRAM],
                    input=json.dumps([rule.pattern, scoped]), text=True,
                    capture_output=True, timeout=1, check=True)
                value = json.loads(proc.stdout)
            except (subprocess.SubprocessError, ValueError):
                value = None
        else:
            value = None
        if value is not None and value != "" and value != []:
            values[field.name] = value
    return values
