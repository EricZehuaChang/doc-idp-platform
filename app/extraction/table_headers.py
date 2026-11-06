"""C2–C3: verify free-form columns against the matched source table, never prose."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.extraction.completeness import _Cell, _SUMMARY, _norm, _read_tables
from app.extraction.rules import _cells
from app.parsers.base import UDR
from app.skillengine.schema import FieldSpec

_POSITIONAL = re.compile(r"列[1-9]\d*")
_NUMBER = re.compile(r"^[+-]?(?:[¥￥$]\s*)?\d[\d.,:%/\-]*\s*(?:元|件|个)?$")
_SEPARATOR = re.compile(r":?-{3,}:?")


@dataclass(frozen=True)
class _Source:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    width: int
    ambiguous_header: bool


def _markdown_tables(udr):
    chunks = [b.text for p in udr.pages for b in p.blocks if b.type == "table"]
    for text in dict.fromkeys([*chunks, udr.full_markdown, *(p.markdown for p in udr.pages)]):
        lines = text.splitlines()
        for i in range(1, len(lines)):
            if "|" not in lines[i - 1] or "|" not in lines[i]:
                continue
            separators = _cells(lines[i])
            if not separators or not all(_SEPARATOR.fullmatch(cell) for cell in separators):
                continue
            table = [("thead", [_Cell(cell, heading=True) for cell in _cells(lines[i - 1])])]
            for line in lines[i + 1:]:
                if "|" not in line:
                    break
                table.append(("tbody", [_Cell(cell) for cell in _cells(line)]))
            yield table


def _source(table, observed: set[tuple], keys: list[str]) -> _Source | None:
    headers, data = (), []
    width = 0
    ambiguous_header = False
    for section, cells in table:
        values = tuple(cell.text.strip() for cell in cells)
        first = next((v for v in values if v), "")
        marked = section == "thead" or all(cell.heading for cell in cells)
        if not first or section == "tfoot" or (not marked and _SUMMARY.match(first)):
            continue
        # A merged title does not describe table width. Some OCR engines wrap
        # the first numbered data row (e.g. 23) in thead/th regardless of content.
        if len(values) == 1 and cells[0].span > 1:
            continue
        width = max(width, sum(cell.span for cell in cells))
        numbers = [v for v in values if _NUMBER.fullmatch(v)]
        numeric_data = bool(numbers)
        same_as_extracted_row = tuple(sorted(map(_norm, values))) in observed
        if (not data and not headers and marked and numbers and not same_as_extracted_row
                and all(re.fullmatch(r"(?:19|20)\d{2}", v) for v in numbers)):
            # Year columns in financial statements are real numeric headers.
            # Without corroborating keys, do not insert them as extracted data.
            if len(set(values) & set(keys)) >= 2:
                numeric_data = False
            else:
                ambiguous_header = True
                continue
        if (not data and not headers and marked and not numeric_data and not same_as_extracted_row
                and all(cell.span == 1 for cell in cells)):
            headers = values
        elif values != headers:
            data.append(values)
    return _Source(headers, tuple(data), width, ambiguous_header) if width else None


def _matched_source(udr: UDR, keys: list[str], rows: list[dict], sole_table: bool) -> tuple[_Source | None, bool]:
    observed = {tuple(sorted(_norm(row.get(k)) for k in keys)) for row in rows}
    sources = dict.fromkeys(source for table in [*_read_tables(udr), *_markdown_tables(udr)]
                            if (source := _source(table, observed, keys)))
    if sole_table and len(sources) == 1:
        return next(iter(sources)), False
    extracted = [{_norm(row.get(k)) for k in keys} - {""} for row in rows]
    candidates = []
    for source in sources:
        parsed = [{_norm(v) for v in r} - {""} for r in source.rows]
        # Associate by values in the same source row. One shared amount in
        # two different tables is insufficient evidence of a match.
        hits = {i for i, values in enumerate(extracted)
                if any(len(values & cells) >= 2 for cells in parsed)}
        named = len(set(keys) & set(source.headers))
        if hits or named >= 2:
            candidates.append((len(hits), named, hits, source))
    candidates.sort(key=lambda x: x[:2], reverse=True)
    if not candidates:
        return None, False
    hits, named, rows_hit, best = candidates[0]
    if any(other[:2] == (hits, named) or other[2] - rows_hit for other in candidates[1:]):
        return None, True
    return best, False


def _column_positions(keys: list[str], rows: list[dict], source: _Source | None) -> tuple[dict[str, int], set[str]]:
    """C2: align complete source rows by values, then resolve physical columns without losing keys."""
    if not source:
        positions = {k: i for i, k in enumerate(keys)}
        conflict = {k for k, i in positions.items() if _POSITIONAL.fullmatch(k) and k != f"列{i + 1}"}
        return positions, conflict

    options = {k: set(range(source.width)) for k in keys}
    data = [tuple(map(_norm, row)) for row in source.rows if len(row) == source.width]
    for key in keys:
        headings = {i for i, h in enumerate(source.headers) if h == key and i < source.width}
        if headings:
            options[key] &= headings
    for row in rows:
        values = {_norm(row.get(k)) for k in keys} - {""}
        if not values:
            continue
        # Rows sharing a single common amount are weak evidence. Only use
        # unique strongest row matches containing multiple independent values.
        scored = [(len(values.intersection(parsed)), parsed) for parsed in data]
        high = max((score for score, _ in scored), default=0)
        matched = [parsed for score, parsed in scored if score == high]
        if high < min(2, len(keys)) or len(matched) != 1:
            continue
        for key in keys:
            value = _norm(row.get(key))
            positions = {i for i, cell in enumerate(matched[0]) if value and value == cell}
            if positions:
                options[key] &= positions

    positions = {}
    while options:
        singles = {k: next(iter(places)) for k, places in options.items() if len(places) == 1}
        if not singles:
            break
        claims = list(singles.values())
        for k, i in singles.items():
            if claims.count(i) == 1:
                positions[k] = i
                del options[k]
        for remaining in options.values():
            remaining.difference_update(claims)
    unresolved = set(options)
    # Ambiguity is reviewable. Preserve every unplaced column in first-seen
    # order, filling the available slots, even if there are extra model keys.
    occupied = set(positions.values())
    free = (i for i in range(max(source.width, len(keys))) if i not in occupied)
    for key in keys:
        if key not in positions:
            positions[key] = next(free)
    return positions, unresolved


def resolve_table_headers(field: FieldSpec, rows: list, udr: UDR, *, sole_table=False) -> tuple[list, dict, list[str]]:
    """Return rows, ordered column metadata, and review reasons for an unspecified-column table.

    Set sole_table only when a document contains one extracted table field
    (and, in List mode, one record). Existing typed tables bypass this path.
    """
    if field.type != "table" or field.columns:
        return rows, {}, []
    objects = [r for r in rows if isinstance(r, dict)]
    keys = list(dict.fromkeys(k for row in objects for k in row if not k.startswith("$")))
    source, ambiguous = _matched_source(udr, keys, objects, sole_table)
    positions, order_uncertain = _column_positions(keys, objects, source)
    keys.sort(key=positions.__getitem__)
    names, origins, reasons = {}, {}, []
    for key in keys:
        i = positions[key]
        original = (key not in order_uncertain and source and i < len(source.headers)
                    and source.headers[i] == key)
        if original and not _POSITIONAL.fullmatch(key):
            name, origin = key, "original"
        elif getattr(field, "infer_headers", False) and not _POSITIONAL.fullmatch(key):
            name, origin = key, "inferred"
        else:
            name, origin = f"列{i + 1}", "positional"
        names[key], origins[name] = name, origin

    normalized = []
    mismatch = len(objects) != len(rows) or ambiguous or bool(order_uncertain)
    for row in objects:
        mismatch |= set(keys) != {k for k in row if not k.startswith("$")}
        out = {names[k]: row.get(k, "") for k in keys}
        out.update({k: v for k, v in row.items() if k.startswith("$")})
        if isinstance(out.get("$cells"), dict):
            out["$cells"] = {names[k]: v for k, v in out["$cells"].items() if k in names}
        normalized.append(out)

    if source:
        mismatch |= len(keys) != source.width or source.ambiguous_header
        model_data = [tuple(_norm(row.get(k)) for k in keys) for row in objects
                      if not _SUMMARY.match(next((str(row[k]) for k in keys if row.get(k)), ""))]
        parsed_data = [tuple(map(_norm, row)) for row in source.rows]
        # Recover the dropped first data row only with a direct, column-aligned
        # match to the following rows. Otherwise keep what was read and flag it.
        if (not source.headers and not source.ambiguous_header and len(parsed_data) >= 2 and model_data
                and len(source.rows[0]) == len(keys) and parsed_data[0] not in model_data
                and parsed_data[1:] == model_data):
            normalized.insert(0, dict(zip(names.values(), source.rows[0])))
            model_data.insert(0, parsed_data[0])
        gap = abs(len(parsed_data) - len(model_data))
        mismatch |= bool(parsed_data) != bool(model_data) or gap >= max(2, (len(parsed_data) + 3) // 4)
    if "inferred" in origins.values():
        reasons.append("table_headers_inferred")
    if mismatch:
        reasons.append("table_shape_mismatch")
    return normalized, {"columns": list(names.values()), "sources": origins}, reasons
