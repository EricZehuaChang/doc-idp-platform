"""9.15 WP3: value formatting — table-driven, including every failure path.
Success never fabricates; failure keeps the raw value and reports an error."""
import pytest

from app.extraction.formatting import format_value, format_table_rows
from app.skillengine.schema import FieldSpec, FieldOutputFormat


def _date(pattern: str) -> FieldSpec:
    return FieldSpec(name="d", type="date",
                     output_format=FieldOutputFormat(date_pattern=pattern))


def _num(places: int) -> FieldSpec:
    return FieldSpec(name="n", type="number",
                     output_format=FieldOutputFormat(decimal_places=places))


@pytest.mark.parametrize("raw,pattern,expect", [
    ("2026-09-17", "YYYY-MM-DD", "2026-09-17"),
    ("2026-09-17", "YYYY/MM/DD", "2026/09/17"),
    ("2026-09-17", "YYYYMMDD", "20260917"),
    ("2026/09/17", "YYYY-MM-DD", "2026-09-17"),
    ("20260917", "YYYY-MM-DD", "2026-09-17"),
    ("17/09/2026", "DD/MM/YYYY", "17/09/2026"),   # input parses, output re-emits same
    ("09/17/2026", "MM/DD/YYYY", "09/17/2026"),
    ("2026年9月17日", "YYYY-MM-DD", "2026-09-17"),
    ("2026年9月17日 14:30", "YYYY-MM-DD HH:mm:ss", "2026-09-17 14:30:00"),
])
def test_date_formats_parse_and_emit(raw, pattern, expect):
    assert format_value(_date(pattern), raw) == (expect, None)


@pytest.mark.parametrize("raw", ["not a date", "2026-13-01", ""])
def test_date_unparseable_keeps_raw_with_error(raw):
    value, err = format_value(_date("YYYY-MM-DD"), raw)
    if raw == "":
        assert err is None            # empty stays empty (no value to fix)
    else:
        assert (value, err) == (raw, err) and err is not None


@pytest.mark.parametrize("raw,places,expect", [
    ("1026.5", 2, "1026.50"),
    ("1,026.505", 2, "1026.51"),          # ROUND_HALF_UP
    ("¥1,026.50", 2, "1026.50"),          # currency symbol + thousands sep
    ("1026", 0, "1026"),
    ("-3.14159", 4, "-3.1416"),
])
def test_number_formats(raw, places, expect):
    assert format_value(_num(places), raw) == (expect, None)


@pytest.mark.parametrize("raw", ["三块钱", "N/A"])
def test_number_unconvertible_keeps_raw_with_error(raw):
    value, err = format_value(_num(2), raw)
    assert value == raw and err is not None


def test_fields_without_format_untouched():
    f = FieldSpec(name="x", type="string")
    assert format_value(f, "anything 1,026.50") == ("anything 1,026.50", None)
    empty = FieldSpec(name="d", type="date",
                      output_format=FieldOutputFormat(date_pattern="YYYY-MM-DD"))
    # a date field whose format is set still formats…
    assert format_value(empty, "2026-09-17") == ("2026-09-17", None)


def test_table_rows_format_in_place_with_raw():
    spec = FieldSpec(name="items", type="table", columns=[
        FieldSpec(name="price", type="number",
                  output_format=FieldOutputFormat(decimal_places=2)),
        FieldSpec(name="note", type="string"),
    ])
    rows = [{"price": "1,026.5", "note": "ok"}, {"price": None, "note": "skip"}]
    out = format_table_rows(rows, spec)
    assert out[0]["price"] == "1026.50"
    assert out[0]["$cells"]["price"]["$raw"] == "1,026.5"
    assert out[1].get("$cells") in (None, {})   # untouched rows stay clean
