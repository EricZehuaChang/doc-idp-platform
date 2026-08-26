"""Studio services: probe drafting, dry-run side-by-side, YAML round-trip,
golden diff logic. LLM mocked throughout."""
from app.parsers.base import UDR, Block, Page
from app.skillengine import studio
from app.skillengine.schema import FieldSpec, SkillPackage

UDR_S = UDR(pages=[Page(page_no=1, blocks=[Block(text="发票号 A1 金额 9.9")])],
            full_markdown="发票号 A1 金额 9.9", parser="t")

PKG = SkillPackage(skill_code="s1", fields=[FieldSpec(name="no"), FieldSpec(name="amt")])


def test_probe_draft_to_fields(monkeypatch):
    monkeypatch.setattr(studio, "chat_json_with_fallback",
                        lambda *a, **k: ({"doc_type": "发票", "fields": [
                            {"name": "invoice_no", "label": "发票号", "type": "string",
                             "instruction": "发票号码", "sample_value": "A1"},
                            {"bad": "no-name-skipped"},
                        ]}, {"prompt_tokens": 1}, "qwen"))
    out = studio.probe(UDR_S)
    fields = studio.draft_to_fields(out["draft"])
    assert len(fields) == 1 and fields[0].name == "invoice_no"
    assert out["provider_used"] == "qwen"


def test_dry_run_side_by_side(monkeypatch):
    calls = []

    def fake_extract(udr, pkg, transport=None, provider_override=None,
                     page_images=None):
        calls.append(provider_override)
        if provider_override == "bad":
            raise RuntimeError("provider down")
        return ({"no": {"$value": "A1", "$confidence": 3, "$bbox": [], "$pages": 1}},
                {"prompt_tokens": 1}, False)
    monkeypatch.setattr(studio, "extract", fake_extract)
    runs = studio.dry_run(UDR_S, PKG, providers=["qwen", "bad"])
    assert [r["ok"] for r in runs] == [True, False]
    assert calls == ["qwen", "bad"]


def test_yaml_round_trip():
    text = studio.package_to_yaml(PKG)
    back = studio.package_from_yaml(text)
    assert back.skill_code == "s1" and [f.name for f in back.fields] == ["no", "amt"]


def test_diff_results_match_rate():
    actual = {"no": {"$value": "A1", "$confidence": 3},
              "amt": {"$value": "9.90", "$confidence": 2}}
    report = studio.diff_results({"no": "A1", "amt": "9.9"}, actual)
    assert report["fields"]["no"]["match"] is True
    assert report["fields"]["amt"]["match"] is False   # 9.90 != 9.9 exact-diff by design
    assert report["matched"] == 1 and report["total"] == 2
