"""PR-B regression tests use synthetic parser output; no model calls or customer documents."""
from copy import deepcopy

import pytest

from app.extraction.completeness import parse_completeness
from app.parsers.base import Block, Page, UDR
from app.skillengine.schema import FieldSpec, SkillPackage


COLS = ("项目", "规格", "单位", "数量", "单价", "金额", "税率", "税额")
GOOD = dict(zip(COLS, ["水泥", "", "吨", "2", "3", "6", "免税", "***"]))
EMPTY = {"review_reasons": [], "table_paths": []}


def table(name="明细", columns=COLS):
    return FieldSpec(name=name, type="table", columns=[FieldSpec(name=c) for c in columns])


def pkg(*fields, **settings):
    return SkillPackage(skill_code="invoice", fields=list(fields) or [table()], **settings)


def row(values, tag="td"):
    return "<tr>" + "".join(f"<{tag}>{value}</{tag}>" for value in values) + "</tr>"


def udr(*tables, parser="glm-ocr-cloud"):
    return UDR(parser=parser, pages=[Page(page_no=1, blocks=[
        Block(type="table", text=html) for html in tables])])


def html(*body):
    return "<table><thead>" + row(COLS, "th") + "</thead><tbody>" + "".join(body) + "</tbody></table>"


def flagged(*paths):
    return {"review_reasons": ["parse_cells_collapsed"], "table_paths": list(paths)}


@pytest.mark.parametrize("broken", ['<tr><td colspan="8"><b>水泥</b></td></tr>',
                                    row(["水泥", "吨"])])
def test_source_structural_evidence_flags_even_a_complete_result_without_rewriting_it(broken):
    source, result = udr(html(broken)), {"明细": [GOOD]}
    original = deepcopy(result)
    assert parse_completeness(source, pkg(), result) == flagged("明细")
    assert result == original


def test_titles_notes_totals_and_one_legitimately_blank_spec_do_not_trigger():
    head = '<tr><td colspan="8">普通发票</td></tr>'
    body = row(GOOD.values())
    footer = '<tr><td colspan="8">合 计（大写） 陆元整</td></tr>'
    section = '<tr><td colspan="8">第二部分：物资</td></tr>'
    text = "<table>" + head + row(COLS, "th") + section + body + footer + "</table>"
    assert parse_completeness(udr(text), pkg(), {"明细": [GOOD]}) == EMPTY
    # A totals-only table can be valid even though there are no line items.
    assert parse_completeness(udr(html('<tr><td colspan="8">合计 6.00</td></tr>')),
                              pkg(), {"明细": []}) == EMPTY


def test_sparse_result_threshold_applies_to_every_row_and_only_original_columns():
    field = table(columns=("项目", "数量", "单价", "金额"))
    field.columns.append(FieldSpec(name="估算", mode="inferred"))
    skill = pkg(field)
    partial = {"项目": "水泥", "数量": 0}
    assert parse_completeness(udr(), skill, {"明细": [partial]}) == EMPTY
    assert parse_completeness(udr(), skill, {"明细": [{"项目": "水泥"}, partial]}) == EMPTY
    assert parse_completeness(udr(), skill, {"明细": [{"项目": "水泥"}]}) == flagged("明细")
    assert parse_completeness(udr(), skill, {"明细": []}) == EMPTY


def test_list_mapping_respects_record_and_table_boundaries():
    source = udr(html('<tr><td colspan="8">水泥</td></tr>'))
    extra = table("付款", columns=("方式", "日期", "付款人"))
    skill = pkg(table(), extra, output_shape="list")
    record = {"明细": [GOOD], "付款": [{"方式": "现金", "日期": "今天", "付款人": "甲"}]}
    result = {"records": [record, {**record, "明细": [{**GOOD, "项目": "砂石"}]}]}
    assert parse_completeness(source, skill, result) == flagged("records[0].明细")
    # Identical printed item names in two records cannot localize a source defect.
    assert parse_completeness(source, skill, {"records": [record, record]}) == flagged()


def test_sparse_field_can_be_identified_without_assigning_other_tables():
    skills = pkg(table(), table("付款", columns=("方式", "日期", "付款人")))
    result = {"明细": [GOOD], "付款": [{"方式": "现金"}]}
    assert parse_completeness(udr(), skills, result) == flagged("付款")


def test_scope_avoids_fast_non_ocr_and_entity_sweeps():
    source, result = udr(parser="pdfplumber"), {"明细": [{"项目": "水泥"}]}
    assert parse_completeness(source, pkg(), result) == EMPTY
    assert parse_completeness(source, pkg(), result, is_image=True) == flagged("明细")
    assert parse_completeness(source, pkg(processing_mode="fast"), result, is_image=True) == EMPTY
    assert parse_completeness(udr(), pkg(table().model_copy(update={"entity_list": True})), result) == EMPTY
    assert parse_completeness(udr(), pkg(table(columns=())), result) == EMPTY
