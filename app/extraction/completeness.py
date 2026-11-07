"""PR-B: flag incomplete OCR tables for review. Detection neither fills values nor calls a model."""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from app.parsers.base import UDR
from app.skillengine.schema import SkillPackage

_OCR = {"glm-ocr-cloud", "rapidocr", "monkeyocr"}
_SUMMARY = re.compile(
    r"^(?:价税合计|小计|合计|总计|备注|说明|制表人|开票人|复核人|收款人|"
    r"grand\s*total\b|sub\s*total\b|total\b|notes?\b)", re.I)


@dataclass
class _Cell:
    text: str
    span: int = 1
    heading: bool = False


class _Tables(HTMLParser):
    """Read visible HTML cell text without executing markup or expanding spans."""
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.section = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
            self.tables.append(self.table)
            self.section = ""
        elif self.table is not None:
            if tag in ("thead", "tbody", "tfoot"):
                self.section = tag
            elif tag == "tr":
                self.row = []
                self.table.append((self.section, self.row))
                self.cell = None
            elif tag in ("td", "th") and self.row is not None:
                span = str(dict(attrs).get("colspan") or "1").strip()
                self.cell = _Cell("", int(span) if re.fullmatch(r"[1-9][0-9]{0,3}", span) else 1,
                                  tag == "th")
                self.row.append(self.cell)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.text += data

    def handle_endtag(self, tag):
        if tag in ("td", "th", "tr", "table"):
            self.cell = None
        if tag in ("tr", "table"):
            self.row = None
        if tag in ("thead", "tbody", "tfoot", "table"):
            self.section = ""
        if tag == "table":
            self.table = None


def _norm(value) -> str:
    return "".join(str(value if value is not None else "").split()).casefold()


def _read_tables(udr):
    # Layout blocks are GLM's primary evidence. Some adapters only return full_markdown.
    blocks = [b.text for page in udr.pages for b in page.blocks if b.type == "table"]
    for text in dict.fromkeys([*blocks, udr.full_markdown, *(p.markdown for p in udr.pages)]):
        if "<table" not in text.lower():
            continue
        parser = _Tables()
        try:
            parser.feed(text)
            parser.close()
        except (AssertionError, ValueError):
            # Malformed OCR output is insufficient structural evidence.
            continue
        yield from parser.tables


def _collapsed_rows(table, column_names):
    """Use a genuine column header; invoice titles above it and totals/footer rows don't count."""
    headings, width = set(), 0
    full_body = False
    pending = []
    for section, row in table:
        names = {_norm(cell.text) for cell in row} - {""}
        if len(names) >= 2 and (section == "thead" or all(c.heading for c in row)
                                or len(names & column_names) >= 2):
            for cells in pending:
                yield headings, cells, full_body
            headings, width = names, sum(c.span for c in row)
            full_body, pending = False, []
            continue
        if not width or not row or section in ("thead", "tfoot"):
            continue
        text = " ".join(c.text.strip() for c in row).strip()
        if not text or _SUMMARY.match(text) or _SUMMARY.match(_norm(text)):
            continue
        if len(row) >= width:
            full_body = True
        wide_cell = len(row) == 1 and row[0].span >= max(2, width - 1)
        short_row = len(row) * 2 < width and sum(c.span for c in row) < width
        if wide_cell or short_row:
            pending.append(names)
    # A full-width section label is ordinary in an otherwise intact table. Wait
    # for the rest of the body so order doesn't change how we assess such rows.
    for cells in pending:
        yield headings, cells, full_body


def parse_completeness(udr: UDR, pkg: SkillPackage, result: dict, *, is_image=False) -> dict:
    """Return review_reasons and affected table_paths; avoid assigning ambiguous evidence."""
    report = {"review_reasons": [], "table_paths": []}
    if pkg.processing_mode != "balanced" or not (
            is_image or udr.parser in _OCR or udr.parser.startswith("vlm:")):
        return report
    columns = {field.name: [c.name for c in field.columns if c.mode == "verbatim" and c.type != "table"]
               for field in pkg.fields if field.type == "table" and not field.entity_list}
    columns = {name: names for name, names in columns.items() if names}
    if not columns:
        return report

    records = result.get("records", []) if pkg.output_shape == "list" else [result]
    targets = {}
    affected = set()
    for i, record in enumerate(records if isinstance(records, list) else []):
        if not isinstance(record, dict):
            continue
        for field, names in columns.items():
            path = f"records[{i}].{field}" if pkg.output_shape == "list" else field
            rows = record.get(field)
            rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            values = [{_norm(row.get(c)) for c in names} - {""} for row in rows]
            targets[path] = (field, set().union(*values))
            if rows and all(sum(not _norm(row.get(c)) for c in names) * 2 > len(names)
                            for row in rows):
                affected.add(path)

    names_by_field = {f: {_norm(c) for c in cols} for f, cols in columns.items()}
    all_names = set().union(*names_by_field.values())
    ambiguous = False
    for table in _read_tables(udr):
        for headings, cells, has_detail_rows in _collapsed_rows(table, all_names):
            matching_fields = {f for f, names in names_by_field.items()
                               if len(headings & names) >= 2}
            candidates = {path for path, (f, _) in targets.items()
                          if not matching_fields or f in matching_fields}
            evidence = {p for p in candidates if cells & targets[p][1]}
            if not evidence and has_detail_rows:
                continue
            if len(evidence) == 1:
                affected.update(evidence)
            elif matching_fields and len(candidates) == 1:
                affected.update(candidates)
            elif matching_fields or evidence:
                # Multiple records/tables match the same parser row. Flag the file only.
                ambiguous = True
    if affected or ambiguous:
        report["review_reasons"].append("parse_cells_collapsed")
        report["table_paths"] = sorted(affected)
    return report
