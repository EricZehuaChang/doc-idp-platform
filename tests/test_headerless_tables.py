"""C2–C3: only matched table headers may vouch for a model's column names."""
from types import SimpleNamespace

from app.extraction.table_headers import resolve_table_headers
from app.parsers.base import Page, UDR


def field(infer=False, columns=()):
    return SimpleNamespace(name="items", type="table", columns=columns, infer_headers=infer)


def table(head, body):
    rows = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row)
                   + "</tr>" for row in body)
    return ("<table><thead><tr>" + "".join(f"<th>{label}</th>" for label in head)
            + "</tr></thead><tbody>" + rows + "</tbody></table>")


def udr(*tables, prose=""):
    return UDR(pages=[Page(page_no=1, blocks=[{"type": "table", "text": html} for html in tables])],
               full_markdown=prose)


def test_first_numbered_row_in_thead_is_data_not_a_semantic_header():
    doc = udr(table(["23", "螺钉", "6.50"], [["24", "螺帽", "8.00"]]), prose="规格说明 综合单价")
    rows = [{"序号": "23", "规格说明": "螺钉", "综合单价": "6.50"},
            {"序号": "24", "规格说明": "螺帽", "综合单价": "8.00"}]
    fixed, meta, reasons = resolve_table_headers(field(), rows, doc)
    assert fixed == [{"列1": "23", "列2": "螺钉", "列3": "6.50"},
                     {"列1": "24", "列2": "螺帽", "列3": "8.00"}]
    assert meta["columns"] == list(fixed[0]) and set(meta["sources"].values()) == {"positional"}
    assert reasons == [] and rows[0]["规格说明"] == "螺钉"


def test_recover_an_unambiguous_missing_first_data_row_verbatim():
    doc = udr(table(["23", "螺钉", "6.50"], [["24", "螺帽", "8.00"]]))
    fixed, _, reasons = resolve_table_headers(field(), [{"序号": "24", "名称": "螺帽", "单价": "8.00"}], doc)
    assert fixed[0] == {"列1": "23", "列2": "螺钉", "列3": "6.50"} and not reasons


def test_opt_in_keeps_unverified_names_and_requires_review():
    doc = udr(table(["23", "螺钉", "6.50"], [["24", "螺帽", "8.00"]]))
    rows = [{"item": "螺钉", "单价": "6.50", "index": "23"}, {"item": "螺帽", "单价": "8.00", "index": "24"}]
    fixed, meta, reasons = resolve_table_headers(field(infer=True), rows, doc)
    assert fixed == rows and list(meta["sources"].values()) == ["inferred"] * 3
    assert reasons == ["table_headers_inferred"]


def test_only_matched_source_headers_promote_names_not_body_words_or_prose():
    doc = udr(table(["类别", "件数"], [["纸张", 7], ["笔", 8]]),
              table(["纸张", "数量"], [["A4", 10], ["A3", 20]]),
              prose="数量和纸张")
    fixed, meta, reasons = resolve_table_headers(field(), [{"纸张": "纸张", "件数": "7"},
                                                           {"纸张": "笔", "件数": "8"}], doc)
    assert fixed[0] == {"列1": "纸张", "件数": "7"}
    assert meta["sources"] == {"列1": "positional", "件数": "original"} and not reasons


def test_ambiguous_source_never_assigns_other_tables_header_or_dimensions():
    doc = udr(table(["name", "quantity"], [["Soup", 1]]),
              table(["name", "count"], [["Soup", 1], ["Tea", 2]]))
    fixed, meta, reasons = resolve_table_headers(field(), [{"name": "Soup", "n": 1}], doc)
    assert meta["sources"] == {"列1": "positional", "列2": "positional"}
    assert fixed[0]["列2"] == 1 and reasons == ["table_shape_mismatch"]


def test_markdown_header_confirmed_from_separate_line_of_dashes():
    doc = UDR(full_markdown="| Item | Qty |\n| --- | ---: |\n| Soup | 1 |")
    rows = [{"Item": "Soup", "Qty": "1"}]
    fixed, meta, reasons = resolve_table_headers(field(), rows, doc)
    assert fixed == rows and meta["sources"] == {"Item": "original", "Qty": "original"} and not reasons


def test_sparse_rows_preserve_values_and_metadata_on_unverified_positional_clash():
    raw = [{"invented": "A", "列1": 1, "$cells": {"invented": {"$confidence": 3}}},
           {"列1": 2, "列3": "B"}]
    fixed, meta, reasons = resolve_table_headers(field(), raw, UDR())
    assert meta["columns"] == ["列1", "列2", "列3"]
    assert fixed == [{"列1": "A", "列2": 1, "列3": "", "$cells": {"列1": {"$confidence": 3}}},
                     {"列1": "", "列2": 2, "列3": "B"}]
    assert reasons == ["table_shape_mismatch"]


def test_shape_mismatch_checks_width_and_significant_row_gap_not_summary_rows():
    doc = udr(table(["序号", "项目", "金额"], [["23", "笔", 6], ["24", "桌", 8], ["25", "椅", 9],
                                                   ["合计", "", 23], ["备注", "", ""], ["总计", "", 23]]))
    _, _, reasons = resolve_table_headers(field(), [{"序号": "23", "项目": "笔"}], doc)
    assert reasons == ["table_shape_mismatch"]
    rows = [{"序号": str(i), "项目": name, "金额": amount} for i, name, amount in
            [(23, "笔", "6"), (24, "桌", "8"), (25, "椅", "9")]]
    _, _, reasons = resolve_table_headers(field(), rows, doc)
    assert reasons == []


def test_explicit_column_table_is_unchanged():
    rows = [{"name": "Soup"}]
    fixed, meta, reasons = resolve_table_headers(field(columns=["name"]), rows, UDR())
    assert fixed is rows and not meta and not reasons


def test_reversed_model_order_maps_to_physical_columns_and_restores_first_row():
    doc = udr(table(["23", "螺钉", "6.50"], [["24", "螺帽", "8.00"]]))
    model = [{"价格": "8.00", "序号": "24", "零件": "螺帽", "$cells": {"价格": {"$confidence": 3}}}]
    fixed, meta, reasons = resolve_table_headers(field(), model, doc)
    assert meta["columns"] == ["列1", "列2", "列3"]
    assert fixed == [{"列1": "23", "列2": "螺钉", "列3": "6.50"},
                     {"列1": "24", "列2": "螺帽", "列3": "8.00", "$cells": {"列3": {"$confidence": 3}}}]
    assert not reasons


def test_physical_order_ambiguous_marks_review_without_losing_keys():
    doc = udr(table(["23", "6", "6"], [["24", "8", "8"]]))
    model = [{"价格": "6", "列2": "6", "编号": "23"}, {"价格": "8", "列2": "8", "编号": "24"}]
    fixed, meta, reasons = resolve_table_headers(field(), model, doc)
    assert meta["columns"] == ["列1", "列2", "列3"]
    assert list(fixed[0].values()) == ["23", "6", "6"] and reasons == ["table_shape_mismatch"]


def test_markdown_separator_does_not_promote_numeric_first_data_row_to_headers():
    doc = UDR(full_markdown="| 23 | 螺钉 | 6.50 |\n| --- | --- | ---: |\n| 24 | 螺帽 | 8.00 |")
    raw = [{"规格说明": "螺帽", "单价": "8.00", "序号": "24"}]
    fixed, meta, reasons = resolve_table_headers(field(), raw, doc)
    assert meta["sources"] == {"列1": "positional", "列2": "positional", "列3": "positional"}
    assert tuple(fixed[0].values()) == ("23", "螺钉", "6.50") and not reasons


def test_numeric_year_headers_not_inserted_as_a_data_row():
    doc = udr(table(["项目", "2025", "2026"], [["收入", "8.00", "9.00"]]))
    rows = [{"项目": "收入", "2025": "8.00", "2026": "9.00"}]
    fixed, meta, reasons = resolve_table_headers(field(), rows, doc)
    assert fixed == rows and list(meta["sources"].values()) == ["original"] * 3 and not reasons
    anonymous = [{"列1": "收入", "列2": "8.00", "列3": "9.00"}]
    fixed, _, reasons = resolve_table_headers(field(), anonymous, doc)
    assert fixed == anonymous and reasons == ["table_shape_mismatch"]
