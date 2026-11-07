"""9.15 WP3: effective_package derivation — the single source the editor,
runner, publish and Playground share."""
from app.skillengine.effective import (category_subpackage, effective_package,
                                       is_v1)
from app.skillengine.schema import SkillPackage


def _v1(**kw) -> SkillPackage:
    return SkillPackage(skill_code="legacy", schema_version=1, **kw)


def _v2(**kw) -> SkillPackage:
    return SkillPackage(skill_code="mixed", schema_version=2, **kw)


def test_v1_package_effectively_unchanged():
    pkg = _v1(fields=[{"name": "a", "type": "string"}])
    assert is_v1(pkg)
    eff = effective_package(pkg)
    assert eff is pkg              # same object: zero transformation


def test_list_fields_keep_native_record_metadata_and_nested_tables():
    pkg = _v2(output_shape="list", fields=[
        {"name": "invoice_no", "type": "string", "required": True},
        {"name": "items", "type": "table", "columns": [{"name": "dish"}]}])
    eff = effective_package(pkg)
    assert eff.fields == pkg.fields and eff.output_shape == "list"
    assert eff.fields is not pkg.fields
    assert effective_package(eff) == eff


def test_fast_forces_never_review_and_drops_challenger():
    pkg = _v2(processing_mode="fast",
              fields=[{"name": "a", "type": "string"}],
              review_policy={"mode": "auto", "confidence_threshold": 2},
              model_binding={"extractor": "GLM", "challenger": "GLM2"})
    eff = effective_package(pkg)
    assert eff.review_policy.mode == "never"
    assert eff.model_binding.challenger is None
    # standard extraction shape preserved
    assert [f.name for f in eff.fields] == ["a"]


def test_category_subpackage_inline_and_classify_only():
    pkg = _v2(skill_mode="advanced", parser="pdfplumber",
              review_policy={"mode": "always"},
              categories=[
                  {"id": "inv", "doc_type": "invoice", "handler": "inline",
                   "fields": [{"name": "invoice_no", "type": "string"}],
                   "validators": [{"type": "required", "field": "invoice_no"}]},
                  {"id": "other", "doc_type": "Other", "is_other": True,
                   "handler": "classify_only"},
              ])
    inv = category_subpackage(pkg, "inv")
    assert inv is not None
    assert [f.name for f in inv.fields] == ["invoice_no"]
    assert inv.validators[0].field == "invoice_no"
    assert inv.parser == "pdfplumber"          # parent-owned trait stays
    assert inv.review_policy.mode == "always"
    assert inv.skill_mode == "standard" and inv.categories == []

    other = category_subpackage(pkg, "other")
    assert other is not None and other.fields == []

    # WP4: an unknown id degrades to the Other category (defensive parity with
    # the classifier's mapping) instead of returning None / raising
    fallback = category_subpackage(pkg, "nope")
    assert fallback is not None and fallback.fields == []


def test_category_subpackage_reference_uses_pinned_package():
    pkg = _v2(skill_mode="advanced",
              categories=[
                  {"id": "ref", "doc_type": "packing", "handler": "existing_skill",
                   "skill_ref": {"skill_code": "packed", "version": 3}},
                  {"id": "other", "doc_type": "Other", "is_other": True,
                   "handler": "classify_only"},
              ])
    pinned = SkillPackage(skill_code="packed", fields=[{"name": "x", "type": "string"}],
                          parser="some_parser")
    sub = category_subpackage(pkg, "ref", pinned_packages={"packed": pinned})
    assert sub is not None
    assert [f.name for f in sub.fields] == ["x"]
    # the referenced skill's own parser pin loses (§ WP4): parent wins
    assert sub.parser is None
    # no pinned package -> unresolvable
    assert category_subpackage(pkg, "ref", pinned_packages={}) is None
