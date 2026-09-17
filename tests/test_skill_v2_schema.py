"""9.15 WP3: DSL v2 schema + validation rules (§3.2/§3.3) — table-driven over
every rule, plus v1 compatibility reads and strict-write rejections."""
import pytest
from pydantic import ValidationError

from app.skillengine.schema import (SkillPackage,
                                    SkillPackageLoose)
from app.skillengine.validation import validate_package


def _pkg(**kw) -> SkillPackage:
    base = dict(skill_code="inv_v2", schema_version=2, name="发票")
    base.update(kw)
    return SkillPackage(**base)


def _field(name: str, **kw) -> dict:
    return {"name": name, **kw}


# —— v1 compatibility ————————————————————————————————————————————————

def test_v1_package_reads_back_unchanged():
    raw = {"skill_code": "v1_skill", "fields": [{"name": "a", "type": "string"}]}
    pkg = SkillPackageLoose(**raw)
    assert pkg.schema_version == 1 and pkg.fields[0].output_format is None
    # defaults appear on dump but semantics unchanged: standard/balanced/object
    assert pkg.skill_mode == "standard" and pkg.processing_mode == "balanced"
    assert pkg.output_shape == "object" and pkg.output.enabled is False


def test_strict_write_rejects_unknown_fields_and_future_version():
    with pytest.raises(ValidationError):
        SkillPackage(skill_code="x", no_such_field=1)
    with pytest.raises(ValidationError, match="高于本环境支持的版本"):
        SkillPackage(skill_code="x", schema_version=3)
    # loose read model tolerates both (stored rows / future writer)
    SkillPackageLoose(skill_code="x", no_such_field=1, schema_version=9)


def test_output_enabled_off_normalizes():
    pkg = _pkg(output={"enabled": True, "action": "off"})
    assert pkg.output.enabled is False       # §3.2: never store both


# —— validation rules (§3.3 + limits) —————————————————————————————————

def test_save_stage_passes_standard_v2():
    pkg = _pkg(fields=[_field("invoice_no", type="string")])
    errors, _ = validate_package(pkg, "save")
    assert errors == []


def test_field_name_rules():
    pkg = _pkg(fields=[_field(""), _field("$x"), _field("a" * 101), _field("ok"),
                       _field("ok")])
    errors, _ = validate_package(pkg, "save")
    paths = [e["path"] for e in errors]
    assert "fields[0].name" in paths and "fields[1].name" in paths
    assert "fields[2].name" in paths and "fields[4].name" in paths


def test_table_nesting_and_list_mode_rules():
    nested = _pkg(fields=[_field("t", type="table",
                                 columns=[_field("c", type="table")])])
    errors, _ = validate_package(nested, "save")
    assert any("嵌套" in e["message"] for e in errors)

    lst = _pkg(output_shape="list", fields=[_field("t", type="table")])
    errors, _ = validate_package(lst, "save")
    assert any("List" in e["message"] for e in errors)


def test_limits_categories_fields_and_rules():
    many_cats = [_field_dict_cat(i) for i in range(51)]
    pkg = _pkg(skill_mode="advanced",
               categories=many_cats + [_other_cat()])
    errors, _ = validate_package(pkg, "save")
    assert any("类别数量超过上限" in e["message"] for e in errors)

    long_rules = _pkg(additional_rules="x" * 8001)
    errors, _ = validate_package(long_rules, "save")
    assert any("附加规则超过" in e["message"] for e in errors)


def _field_dict_cat(i):
    return {"id": f"c{i}", "doc_type": f"t{i}", "handler": "inline",
            "fields": [{"name": "f", "type": "string"}]}


def _other_cat():
    return {"id": "other", "doc_type": "Other", "is_other": True,
            "handler": "classify_only"}


def test_advanced_requires_exactly_one_other():
    pkg = _pkg(skill_mode="advanced", categories=[_field_dict_cat(0)])
    errors, _ = validate_package(pkg, "save")
    assert any("Other" in e["message"] for e in errors)

    pkg2 = _pkg(skill_mode="advanced",
                categories=[_field_dict_cat(0), _other_cat()])
    errors2, _ = validate_package(pkg2, "save")
    assert not any("Other" in e["message"] for e in errors2)


def test_other_doc_type_immutable_and_dup_types_rejected():
    pkg = _pkg(skill_mode="advanced",
               categories=[{"id": "a", "doc_type": "invoice", "handler": "inline",
                            "fields": []},
                           {"id": "b", "doc_type": "invoice", "handler": "inline",
                            "fields": []},
                           _other_cat()])
    errors, _ = validate_package(pkg, "save")
    assert any("doc_type 必须唯一" in e["message"] for e in errors)

    pkg2 = _pkg(skill_mode="advanced",
                categories=[{"id": "o", "doc_type": "Renamed", "is_other": True,
                             "handler": "classify_only"}])
    errors2, _ = validate_package(pkg2, "save")
    assert any("固定为 Other" in e["message"] for e in errors2)


def test_publish_gates_inline_fields_and_fast_mode():
    cat = {"id": "a", "doc_type": "invoice", "handler": "inline", "fields": []}
    pkg = _pkg(skill_mode="advanced", categories=[cat, _other_cat()])
    errors, _ = validate_package(pkg, "publish")
    assert any("至少要有一个字段" in e["message"] for e in errors)
    # save stage tolerates it (draft in progress)
    errors_save, _ = validate_package(pkg, "save")
    assert not any("至少要有一个字段" in e["message"] for e in errors_save)

    fast = _pkg(processing_mode="fast", fields=[])
    errors_f, _ = validate_package(fast, "publish")
    assert any(e.get("code") == "fast_mode_requires_standard_fields" for e in errors_f)

    fast_sweep = _pkg(processing_mode="fast",
                      fields=[_field("names", type="table", entity_list=True)])
    errors_fs, _ = validate_package(fast_sweep, "publish")
    assert any("entity_list" in e["message"] for e in errors_fs)


def test_existing_skill_reference_resolution():
    cat = {"id": "a", "doc_type": "invoice", "handler": "existing_skill",
           "skill_ref": {"skill_code": "packed", "version": 3}}
    pkg = _pkg(skill_mode="advanced", categories=[cat, _other_cat()])

    errors, _ = validate_package(pkg, "publish",
                                 resolver=lambda code: {"version": 3,
                                                        "skill_mode": "standard"})
    assert not any(e.get("code") == "reference_unavailable" for e in errors)

    errors2, _ = validate_package(pkg, "publish", resolver=lambda code: None)
    assert any(e.get("code") == "reference_unavailable" for e in errors2)

    errors3, _ = validate_package(pkg, "publish",
                                  resolver=lambda code: {"version": 1,
                                                         "skill_mode": "advanced"})
    assert any(e.get("code") == "reference_unavailable" for e in errors3)


def test_v1_package_skips_v2_validation():
    pkg = SkillPackage(skill_code="legacy", schema_version=1,
                       fields=[_field("$weird")])
    errors, _ = validate_package(pkg, "publish")
    assert errors == []      # legacy packages keep their old (permissive) rules


# —— v1 compile snapshot (§ WP3 兼容门控) ————————————————————————————

def test_v1_package_upgraded_to_v2_compiles_identically():
    """Opening a v1 skill in the new editor and saving writes schema_version=2
    with defaults; the prompt the runner sends MUST NOT change."""
    from app.parsers.base import Block, Page, UDR
    from app.skillengine.compiler import compile_messages

    v1 = SkillPackage(skill_code="snap", schema_version=1,
                      doc_type_hint="invoice",
                      fields=[{"name": "invoice_no", "type": "string",
                               "instruction": "发票号码", "required": True}],
                      additional_rules="保留前导零")
    udr = UDR(pages=[Page(page_no=1, width=100, height=100,
                          blocks=[Block(text="INV-1", bbox=[1, 2, 3, 4])])],
              full_markdown="INV-1")

    v2_raw = v1.model_dump()
    v2_raw["schema_version"] = 2        # what the editor save does
    v2 = SkillPackage(**v2_raw)

    assert compile_messages(v1, udr) == compile_messages(v2, udr), \
        "v1→v2 upgrade must not drift the prompt"

