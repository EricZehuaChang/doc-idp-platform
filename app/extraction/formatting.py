"""Display formatting for extracted values (9.15 R11) — PURE CORE.

Order matters (§ WP3): the pipeline scores/locates the RAW value first, then
applies this module. On success `$value` carries the formatted value and `$raw`
the original (additive key); on failure `$value` keeps the original, a
`$format_error` is recorded and confidence is capped at 1 so auto-review picks
the file up. We never fabricate a value we could not parse.

Money uses Decimal (ROUND_HALF_UP); dates only parse the patterns listed in
the DSL plus common Chinese writing ("2026年9月17日"). No eval, no templates.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.skillengine.schema import FieldSpec

_NUM_CLEAN = re.compile(r"[,\s￥¥$€£]")
_DATE_PATTERNS = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S",
                  "%d/%m/%Y", "%m/%d/%Y"]
_OUT = {"YYYY-MM-DD": "%Y-%m-%d", "YYYY/MM/DD": "%Y/%m/%d", "YYYYMMDD": "%Y%m%d",
        "YYYY-MM-DD HH:mm:ss": "%Y-%m-%d %H:%M:%S", "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y"}
_CN_DATE = re.compile(
    r"^(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


def _fmt_date(value: str, pattern: str) -> tuple[str, str | None]:
    v = value.strip()
    dt: datetime | None = None
    for fmt in _DATE_PATTERNS:
        try:
            dt = datetime.strptime(v, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        m = _CN_DATE.match(v)
        if m:
            y, mo, d, h, mi, s = (m.group(1), m.group(2), m.group(3),
                                  m.group(4) or "0", m.group(5) or "0",
                                  m.group(6) or "0")
            try:
                dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
            except ValueError:
                dt = None
    if dt is None:
        return value, f"无法按 {pattern} 解析日期"
    return dt.strftime(_OUT[pattern]), None


def _fmt_number(value: str, places: int) -> tuple[str, str | None]:
    v = _NUM_CLEAN.sub("", value.strip())
    if not v:
        return value, "金额为空，无法量化小数位"
    try:
        q = Decimal(v).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return value, "不是可转换的数值"
    return f"{q:f}", None


def format_value(field: FieldSpec, value: str) -> tuple[str, str | None]:
    """Returns (formatted_value, error). error=None means formatted (or the
    value needed no formatting — returned unchanged)."""
    of = field.output_format
    if of is None or not value:
        return value, None
    if field.type == "date" and of.date_pattern:
        return _fmt_date(value, of.date_pattern)
    if field.type == "number" and of.decimal_places is not None:
        return _fmt_number(value, of.decimal_places)
    return value, None


def format_table_rows(rows: list, spec: FieldSpec) -> list:
    """Table columns format in place: the row keeps the formatted value and
    `$cells[col].$raw` preserves the original (additive key only)."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        for c in spec.columns:
            v = row.get(c.name)
            if v is None or isinstance(v, (dict, list)):
                continue
            formatted, err = format_value(c, str(v))
            if err is None and formatted != str(v):
                row[c.name] = formatted
                row.setdefault("$cells", {}).setdefault(c.name, {})["$raw"] = str(v)
    return rows
