"""PR-A: exercise native List contracts and record-local extraction without provider calls."""
import hashlib
import json

import pytest

from app.extraction import pipeline
from app.extraction.record_geometry import annotate_record_regions
from app.parsers.base import Page, UDR
from app.skillengine.compiler import MAX_DOC_CHARS, compile_messages
from app.skillengine.schema import SkillPackage


def package(shape="list", **kwargs):
    return SkillPackage(skill_code="receipts", output_shape=shape,
                        fields=[{"name": "receipt_no"},
                                {"name": "items", "type": "table",
                                 "columns": [{"name": "dish"}, {"name": "quantity", "type": "number"}]}],
                        model_binding={"extractor": "primary", "challenger": kwargs.pop("challenger", None)},
                        **kwargs)


def transport(monkeypatch, responses):
    calls = []
    replies = iter(responses)

    def send(messages, chain, transport=None):
        calls.append((messages, chain))
        return next(replies), {"prompt_tokens": 10, "completion_tokens": 2}, chain[0]

    monkeypatch.setattr(pipeline, "chat_json_with_fallback", send)
    return calls


def test_object_prompt_baseline_and_native_list_contract():
    sample = UDR(full_markdown="Two receipts")
    baseline = json.dumps(compile_messages(package("object"), sample), ensure_ascii=False, separators=(",", ":"))
    # SHA from main 98fff53, including whitespace and message roles.
    assert hashlib.sha256(baseline.encode()).hexdigest() == "559577f8c29b25fb76c1b58a9f035806731725d1cf1fe59302feaf6359cca6d5"
    messages = compile_messages(package(), sample)
    assert "禁止跨单据" in messages[0]["content"]
    contract = json.loads(messages[-1]["content"].split("\n")[1])
    assert contract == {"records": [{"receipt_no": "...", "items": [{"dish": "...", "quantity": "..."}]}]}


def receipts():
    return UDR(pages=[Page(page_no=1, width=250, height=150, blocks=[
        {"text": "A01", "bbox": [0, 0, 30, 12]}, {"text": "Soup", "bbox": [0, 30, 30, 40]},
        {"text": "B02", "bbox": [110, 0, 140, 12]}, {"text": "Tea", "bbox": [110, 30, 140, 40]}]),
        Page(page_no=2, markdown="End")])


def rows():
    return [{"receipt_no": "A01", "items": [{"dish": "Soup"}]},
            {"receipt_no": "B02", "items": [{"dish": "Tea"}]}]


def test_records_each_keep_their_items_locations_and_confidence(monkeypatch):
    calls = transport(monkeypatch, [{"records": rows()}])
    result, usage, review = pipeline.extract(receipts(), package(
        validators=[{"type": "regex", "field": "receipt_no", "pattern": "^A.*"}]))
    assert len(calls) == 1
    a, b = result["records"]
    assert a["receipt_no"]["$confidence"] == 3 and "$rule_failures" not in a["receipt_no"]
    assert b["receipt_no"]["$confidence"] == 0 and b["receipt_no"]["$rule_failures"]
    assert [r["items"][0]["dish"] for r in (a, b)] == ["Soup", "Tea"]
    assert a["items"][0]["$cells"]["dish"]["$hits"][0]["bbox"] == [0, 30, 30, 40]
    assert a["$record_bbox"] == [{"page": 1, "bbox": [0, 0, 30, 40]}]
    assert usage["review_reasons"] == [] and review


@pytest.mark.parametrize("reply", [{}, {"records": "wrong type"}, {"records": []}])
def test_empty_forces_review_even_fast_never(monkeypatch, reply):
    transport(monkeypatch, [reply])
    result, usage, review = pipeline.extract(receipts(), package(review_policy={"mode": "never"}), fast=True)
    assert result == {"records": []}
    assert review and usage["review_reasons"] == ["records_empty"]


def test_challenger_alignment_and_count_mismatch(monkeypatch):
    replies = [{"records": rows()}, {"records": [rows()[0]]}]
    calls = transport(monkeypatch, replies)
    result, usage, review = pipeline.extract(receipts(), package(challenger="other", review_policy={"mode": "never"}))
    assert [chain for _, chain in calls] == [["primary"], ["other"]]
    a, b = result["records"]
    assert a["receipt_no"]["$challenger"]["agree"] and "$challenger" not in b["receipt_no"]
    assert max(r["receipt_no"]["$confidence"] for r in (a, b)) <= 1
    assert a["items"][0]["$cells"]["dish"]["$confidence"] == 1
    assert usage["challenger_record_count_mismatch"] and review
    assert usage["review_reasons"] == ["challenger_record_count_mismatch"]


def test_locations_trigger_review_only_on_the_same_page(monkeypatch):
    overlapping = rows()
    overlapping[1]["receipt_no"] = "A01"
    overlapping[1]["items"][0]["dish"] = "Soup"
    transport(monkeypatch, [{"records": overlapping}])
    _, usage, review = pipeline.extract(receipts(), package(review_policy={"mode": "never"}))
    assert review and usage["review_reasons"] == ["records_overlap"]
    # Tight overlap of the detail cell with another receipt's header
    # must trigger even when the aggregate bounds have low IoU.
    a = {"name": {"$pages": 1, "$bbox": [0, 0, 20, 10]}}
    b = {"name": {"$pages": 1, "$bbox": [900, 0, 920, 10]},
         "items": [{"$cells": {"dish": {"$hits": [{"page": 1, "bbox": [7, 2, 10, 5]}]}}}]}
    assert annotate_record_regions([a, b])
    assert not annotate_record_regions([a, {"name": {"$pages": 2, "$bbox": [0, 0, 20, 10]}}])


def test_long_list_large_page_no_truncation_and_no_duplicate_row_loss(monkeypatch):
    text = "a" * MAX_DOC_CHARS + "b" * MAX_DOC_CHARS + "last"
    first = {"receipt_no": "A01", "items": [{"dish": "Soup"}]}
    calls = transport(monkeypatch, [
        {"records": [first]},
        {"records": [{**first, "$continuation": True}]},
        {"records": [{**first, "$continuation": False}]},
    ])
    result, usage, _ = pipeline.extract(UDR(full_markdown=text, pages=[Page(page_no=1)]), package(),
                                        page_images={1: "image-1"})
    pieces = []
    image_count = 0
    for messages, _ in calls:
        content = messages[-1]["content"]
        if isinstance(content, list):
            image_count += len(content) - 1
            content = content[0]["text"]
        pieces.append(content.split("## 文档内容\n", 1)[1])
    assert "".join(pieces) == text and max(map(len, pieces)) <= MAX_DOC_CHARS
    assert len(calls) == 3 and image_count == 1
    assert usage["prompt_tokens"] == 30 and usage["review_reasons"] == []
    assert [len(r["items"]) for r in result["records"]] == [2, 1]
    assert "$continuation" not in str(result)


@pytest.mark.parametrize(("marker", "second"), [(None, "A01"), (True, "conflicting")])
def test_long_ambiguous_boundary_keeps_both_records_and_forces_review(monkeypatch, marker, second):
    first = {"receipt_no": "A01", "items": []}
    transport(monkeypatch, [{"records": [first]},
                            {"records": [{"receipt_no": second, "items": [], "$continuation": marker}]}])
    result, usage, review = pipeline.extract(UDR(full_markdown="x" * (MAX_DOC_CHARS + 1)),
                                             package(review_policy={"mode": "never"}))
    assert len(result["records"]) == 2
    assert usage["review_reasons"] == ["records_boundary_uncertain"] and review


def test_many_rasters_in_order_without_truncation(monkeypatch):
    pages = [Page(page_no=i + 1) for i in range(10)]
    images = {p.page_no: f"image-{p.page_no}" for p in pages}
    calls = transport(monkeypatch, [{"records": [rows()[0]]}, {"records": [{**rows()[1], "$continuation": False}]}])
    pipeline.extract(UDR(pages=pages), package(), page_images=images)
    seen = [[part["image_url"]["url"] for part in m[-1]["content"][1:]] for m, _ in calls]
    assert [len(p) for p in seen] == [8, 2]
    assert seen[0] + seen[1] == list(images.values())


@pytest.mark.parametrize("record", ["bad-row", {"receipt_no": "A01"},
                                    {"receipt_no": "A01", "items": None},
                                    {"receipt_no": "A01", "items": ["bad-item"]}])
def test_malformed_record_requires_review_under_never_policy(monkeypatch, record):
    transport(monkeypatch, [{"records": [record]}])
    _, usage, review = pipeline.extract(receipts(), package(review_policy={"mode": "never"}), fast=True)
    assert usage["review_reasons"] == ["records_invalid"] and review


@pytest.mark.parametrize("required", [True, False])
def test_empty_table_required_vs_optional(monkeypatch, required):
    pkg = package(review_policy={"mode": "never"})
    pkg.fields[1].required = required
    transport(monkeypatch, [{"records": [{"receipt_no": "A01", "items": []}]}])
    _, usage, review = pipeline.extract(receipts(), pkg)
    assert review == required
    assert usage["review_reasons"] == (["records_invalid"] if required else [])


def test_intermediate_record_structure_not_hidden_by_merge(monkeypatch):
    text = "x" * (MAX_DOC_CHARS + 1)
    transport(monkeypatch, [
        {"records": [{"receipt_no": "A01", "items": "not a table"}]},
        {"records": [{"$continuation": True, "items": [{"dish": "Soup"}]}]}])
    result, usage, review = pipeline.extract(UDR(full_markdown=text), package(review_policy={"mode": "never"}))
    assert len(result["records"]) == 1 and review and "records_invalid" in usage["review_reasons"]


def test_receipt_continues_across_page_chunks_with_repeated_headers(monkeypatch):
    pages = [Page(page_no=i + 1, markdown=f"Receipt A01\nitem {i}") for i in range(9)]
    doc = UDR(pages=pages, full_markdown="\n\n".join(p.markdown for p in pages))
    images = {p.page_no: f"image-{p.page_no}" for p in pages}
    calls = transport(monkeypatch, [
        {"records": [{"receipt_no": "A01", "items": [{"dish": "Soup"}]}]},
        {"records": [{"$continuation": True, "items": [{"dish": "Tea"}]}]}])
    result, usage, _ = pipeline.extract(doc, package(), page_images=images)
    assert len(calls) == 2
    texts = [m[-1]["content"][0]["text"].split("## 文档内容\n")[1] for m, _ in calls]
    assert "".join(texts) == doc.full_markdown
    assert [row["dish"] for row in result["records"][0]["items"]] == ["Soup", "Tea"]
    assert len(result["records"]) == 1 and usage["review_reasons"] == []


def test_same_count_challenger_disagrees_only_on_second_record(monkeypatch):
    second_opinion = rows()
    second_opinion[1]["receipt_no"] = "B03"
    transport(monkeypatch, [{"records": rows()}, {"records": second_opinion}])
    result, usage, review = pipeline.extract(receipts(), package(challenger="other"))
    first, second = [r["receipt_no"] for r in result["records"]]
    assert first["$confidence"] == 3 and first["$challenger"]["agree"]
    assert second["$confidence"] == 1 and not second["$challenger"]["agree"]
    assert not usage["review_reasons"] and review
