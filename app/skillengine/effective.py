"""Effective execution configuration (9.15 § WP3) — PURE CORE.

`effective_package(pkg)` is the SINGLE place that derives what actually runs
from what is configured. Editor validation, publish, Playground and the runner
all call this one function so no surface re-derives modes ad hoc.

- v1 (schema_version 1, plain standard)     -> returned unchanged.
- List output shape (R09)                   -> fields fold into one `records`
  table column, so the compiler and extraction pipeline need no changes.
- fast (WP5)                                -> standard extraction, review
  forced to never, challenger disabled.
- advanced (WP4)                            -> per-category subpackages.
"""
from copy import deepcopy

from app.skillengine.schema import FieldSpec, ReviewPolicy, SkillPackage


def is_v1(pkg: SkillPackage) -> bool:
    return pkg.schema_version < 2 and pkg.skill_mode == "standard" \
        and pkg.output_shape == "object" and not pkg.output.enabled


def effective_package(pkg: SkillPackage) -> SkillPackage:
    """Standard/balanced v1 and v2-standard packages run as-is."""
    if is_v1(pkg):
        return pkg
    eff = deepcopy(pkg)
    if eff.output_shape == "list" and eff.fields:
        # List mode: wrap the configured fields as columns of one table so the
        # whole pipeline keeps its "extract fields of a package" shape.
        if not (len(eff.fields) == 1 and eff.fields[0].type == "table"
                and eff.fields[0].name == "records"):
            eff.fields = [FieldSpec(
                name="records", type="table", instruction="按文档中出现顺序逐条列出",
                columns=deepcopy(pkg.fields))]
    if eff.processing_mode == "fast":
        # fast never reviews and never pays for a challenger channel (WP5)
        eff.review_policy = ReviewPolicy(mode="never",
                                         confidence_threshold=eff.review_policy.confidence_threshold)
        eff.model_binding.challenger = None
    return eff


def category_subpackage(pkg: SkillPackage, category_id: str,
                        pinned_packages: dict[str, SkillPackage] | None = None
                        ) -> SkillPackage | None:
    """Advanced mode (WP4): derive the executable package for one category.
    `pinned_packages` maps referenced skill codes to their frozen packages
    (resolved by the caller); inline categories derive from this package."""
    cat = next((c for c in pkg.categories if c.id == category_id), None)
    if cat is None:
        return None
    if cat.handler == "existing_skill" and cat.skill_ref:
        ref = (pinned_packages or {}).get(cat.skill_ref.skill_code)
        if ref is None:
            return None
        sub = deepcopy(ref)
    elif cat.handler == "classify_only":
        sub = deepcopy(pkg)
        sub.fields = []
        return sub
    else:                                   # inline
        sub = deepcopy(pkg)
        sub.fields = deepcopy(cat.fields)
        sub.output_shape = cat.output_shape
    # parent-owned traits stay with the parent (§ WP4): review policy, parser,
    # validators of the category; the referenced skill's own parser pin loses
    sub.review_policy = deepcopy(pkg.review_policy)
    sub.parser = pkg.parser
    sub.validators = deepcopy(cat.validators)
    sub.additional_rules = cat.additional_rules or sub.additional_rules
    sub.skill_mode = "standard"             # subpackages execute as standard
    sub.categories = []
    return sub
