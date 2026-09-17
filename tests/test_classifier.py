"""9.15 WP4: the advanced-mode document classifier (pure module, LLM mocked).

Covers the four file layouts, the 40-page windowing with 1-page overlap
(later window wins), output validation (every page once, unknown category ->
Other, page 1 starts a doc), retry-then-fail, and the prompt excerpt cap.
"""
import pytest

from app.extraction import classifier
from app.extraction.classifier import ClassificationError, plan_documents
from app.parsers.base import Block, Page, UDR

CATS = [
    {"id": "invoice", "doc_type": "发票", "recognition_instruction": "有发票号码和金额",
     "is_other": False},
    {"id": "receipt", "doc_type": "收据", "recognition_instruction": "", "is_other": False},
    {"id": "Other", "doc_type": "Other", "recognition_instruction": "", "is_other": True},
]


def udr(n: int) -> UDR:
    return UDR(pages=[Page(page_no=i, width=595, height=842,
                           blocks=[Block(text=f"p{i} 发票号 INV-{i}")])
                     for i in range(1, n + 1)],
               full_markdown="", parser="t")


class FakeChat:
    """Programmable chat_json_with_fallback: pops scripted outputs per call."""
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, messages, providers, transport=None):
        self.calls.append(messages[1]["content"])
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, {"prompt_tokens": 3, "completion_tokens": 4}, "fake"


def test_single_layout_whole_file_one_doc(monkeypatch):
    fake = FakeChat([{"category_id": "invoice"}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, usage = plan_documents(udr(3), CATS, "single")
    assert plan == [{"pages": [1, 2, 3], "category_id": "invoice"}]
    assert usage["provider_used"] == "fake"
    # only the first SINGLE_LOOK_PAGES excerpts go into the prompt
    assert "第3页开头" in fake.calls[0] and "第4页开头" not in fake.calls[0]


def test_same_type_independent_one_doc_per_page(monkeypatch):
    fake = FakeChat([{"category_id": "receipt"}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(3), CATS, "same_type_independent")
    assert [d["pages"] for d in plan] == [[1], [2], [3]]
    assert all(d["category_id"] == "receipt" for d in plan)


def test_mixed_layout_merges_consame_pages(monkeypatch):
    # p1 invoice(new), p2 invoice(continue), p3 receipt(new) -> 2 docs
    fake = FakeChat([{"pages": [
        {"page": 1, "category_id": "invoice", "new_doc": True},
        {"page": 2, "category_id": "invoice", "new_doc": False},
        {"page": 3, "category_id": "receipt", "new_doc": True}]}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(3), CATS, "mixed")
    assert plan == [{"pages": [1, 2], "category_id": "invoice"},
                    {"pages": [3], "category_id": "receipt"}]


def test_mixed_same_category_new_doc_starts_new_document(monkeypatch):
    fake = FakeChat([{"pages": [
        {"page": 1, "category_id": "invoice", "new_doc": True},
        {"page": 2, "category_id": "invoice", "new_doc": True},
        {"page": 3, "category_id": "invoice", "new_doc": False}]}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(3), CATS, "mixed")
    assert [d["pages"] for d in plan] == [[1], [2, 3]]


def test_continuous_layout_groups_by_new_doc_marks(monkeypatch):
    fake = FakeChat([{"category_id": "invoice", "pages": [
        {"page": 1, "new_doc": True}, {"page": 2, "new_doc": False},
        {"page": 3, "new_doc": True}, {"page": 4, "new_doc": False}]}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(4), CATS, "same_type_continuous")
    assert [d["pages"] for d in plan] == [[1, 2], [3, 4]]


def test_unknown_category_falls_back_to_other(monkeypatch):
    fake = FakeChat([{"pages": [
        {"page": 1, "category_id": "nope", "new_doc": True},
        {"page": 2, "category_id": "invoice", "new_doc": False}]}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(2), CATS, "mixed")
    assert plan[0]["category_id"] == "Other"


def test_invalid_output_retries_then_raises(monkeypatch):
    # first call misses page 2, retry (stricter prompt) misses it again
    fake = FakeChat([
        {"pages": [{"page": 1, "category_id": "invoice", "new_doc": True}]},
        {"pages": [{"page": 1, "category_id": "invoice", "new_doc": True},
                    {"page": 1, "category_id": "invoice", "new_doc": False}]},  # dup
    ])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    with pytest.raises(ClassificationError):
        plan_documents(udr(2), CATS, "mixed")
    assert len(fake.calls) == 2 and "重试要求" in fake.calls[1]


def test_retry_recovers_from_bad_first_output(monkeypatch):
    fake = FakeChat([
        "not json at all",
        {"pages": [{"page": 1, "category_id": "receipt", "new_doc": True}]},
    ])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(1), CATS, "mixed")
    assert plan == [{"pages": [1], "category_id": "receipt"}]


def test_windowing_over_40_pages_overlap_later_window_wins(monkeypatch):
    # 45 pages -> windows [1..40], [40..45]; page 40 decided by window 2
    outs = [
        {"pages": [{"page": p, "category_id": "invoice", "new_doc": True}
                    for p in range(1, 41)]},
        {"pages": [{"page": 40, "category_id": "receipt", "new_doc": True}] +
                  [{"page": p, "category_id": "receipt",
                    "new_doc": p == 41} for p in range(41, 46)]},
    ]
    fake = FakeChat(outs)
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan, _ = plan_documents(udr(45), CATS, "mixed")
    page40 = next(d for d in plan if 40 in d["pages"])
    assert page40["category_id"] == "receipt"          # later window wins
    covered = sorted(p for d in plan for p in d["pages"])
    assert covered == list(range(1, 46))               # full coverage, no dup


def test_classification_error_when_no_categories_or_empty(monkeypatch):
    with pytest.raises(ClassificationError):
        plan_documents(udr(1), [], "mixed")
    with pytest.raises(ClassificationError):
        plan_documents(UDR(pages=[], full_markdown="", parser="t"), CATS, "mixed")


def test_prompt_excerpt_capped_at_600_chars(monkeypatch):
    big = UDR(pages=[Page(page_no=1, width=1, height=1,
                          blocks=[Block(text="x" * 5000)])],
              full_markdown="", parser="t")
    fake = FakeChat([{"category_id": "invoice"}])
    monkeypatch.setattr(classifier, "chat_json_with_fallback", fake)
    plan_documents(big, CATS, "single")
    assert "x" * 601 not in fake.calls[0]
