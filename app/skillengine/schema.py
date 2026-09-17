"""SkillPackage — the compiled, versioned contract of a skill (design v0.2 §5.1).
"Sample-as-model": 1-2 samples become few-shot pairs + anchor hints; extraction
relies on prompt engineering + JSON schema hard constraint, never training.
Field dual mode (§5.6): verbatim values must be locatable in source text;
inferred values (classification/derivation/suggestion) must carry reasoning.

9.15 DSL v2 (§3.2): new fields only — v1 packages read back unchanged
(schema_version defaults to 1). Strict/loose split: WRITES (save, publish,
import) parse with the strict model (extra="forbid", schema_version > 2
rejected) so unknown/newer fields never silently vanish; READS of stored rows
use SkillPackageLoose which tolerates extra keys and future versions.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class FieldOutputFormat(BaseModel):
    """Finite display-format hints — data descriptions, not executable rules."""
    date_pattern: Literal["YYYY-MM-DD", "YYYY/MM/DD", "YYYYMMDD",
                          "YYYY-MM-DD HH:mm:ss", "DD/MM/YYYY", "MM/DD/YYYY"] | None = None
    decimal_places: int | None = Field(default=None, ge=0, le=6)


class FieldSpec(BaseModel):
    name: str
    type: str = "string"                 # string|number|date|enum|table
    instruction: str = ""                # natural-language extraction rule (user-authored)
    mode: str = "verbatim"               # verbatim | inferred (§5.6)
    required: bool = False
    anchor_hints: list[str] = Field(default_factory=list)
    enum_values: list[str] = Field(default_factory=list)
    columns: list["FieldSpec"] = Field(default_factory=list)   # for type=table
    # entity-list table (PII/masking sweeps): rows are document-wide hits, not a
    # layout table — exempt from the paid-OCR table escalation (§4.3) and the
    # compiler adds a full-document enumeration instruction.
    entity_list: bool = False
    # 9.15: display formatting applied AFTER confidence scoring (§ WP3)
    output_format: FieldOutputFormat | None = None


class SkillRef(BaseModel):
    """Pin an existing published skill as one category's extractor (R10).
    version=None means "follow the latest published version"; publishing
    freezes it to the concrete number."""
    skill_code: str
    version: int | None = None


class Validator(BaseModel):
    """Rule channel of confidence (§5.3). M1 structured forms (no string DSL):
    - {type: regex, field, pattern}
    - {type: required, field}
    - {type: sum_equals, target, parts[]}  (invoice arithmetic checks)
    """
    type: str
    field: str | None = None
    pattern: str | None = None
    target: str | None = None
    parts: list[str] = Field(default_factory=list)


class FewShot(BaseModel):
    input_excerpt: str
    expected_output: dict


class ReviewPolicy(BaseModel):
    mode: str = "auto"                   # auto|always|never (Insavlo "Needs Review Mode")
    confidence_threshold: int = 2        # 0-3 scale; below -> needs review


class ModelBinding(BaseModel):
    extractor: str = ""                  # provider name; empty -> platform active
    fallback: str | None = None
    challenger: str | None = None        # optional arbitration channel (§5.3)


class CategorySpec(BaseModel):
    """One document class of an advanced-mode skill (§3.2). `id` is the
    immutable internal identity — renaming doc_type never orphans fields."""
    id: str
    doc_type: str = ""
    recognition_instruction: str = ""
    is_other: bool = False
    handler: Literal["inline", "existing_skill", "classify_only"] = "inline"
    fields: list[FieldSpec] = Field(default_factory=list)
    validators: list[Validator] = Field(default_factory=list)
    additional_rules: str = ""
    output_shape: Literal["object", "list"] = "object"
    skill_ref: SkillRef | None = None


class OutputConfig(BaseModel):
    """File artifacts on completion (R12/R13, WP6). One `action` encodes the
    mutually-exclusive behavior; enabled+off normalizes to enabled=false."""
    enabled: bool = False
    action: Literal["off", "rename", "split"] = "off"
    naming_rule: str = ""
    searchable_pdf: bool = False

    _normalized_off: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def _normalize(self):
        if self.enabled and self.action == "off":
            self.enabled = False
            self._normalized_off = True
        return self


class _SkillPackageCore(BaseModel):
    """All fields, shared by the strict write model and the loose read model."""
    skill_code: str
    schema_version: int = 1              # 9.15: stored v1 rows stay 1; editor saves 2
    name: str = ""
    description: str = ""                # editor "描述" (human context, not prompt)
    kind: str = "extract"                # extract|audit (§5.5, audit lands M3)
    doc_type_hint: str = ""
    # —— v2 execution shape ——
    processing_mode: Literal["balanced", "fast"] = "balanced"
    skill_mode: Literal["standard", "advanced"] = "standard"
    document_layout: Literal["single", "mixed", "same_type_independent",
                             "same_type_continuous"] = "single"
    classification_rules: str = ""
    categories: list[CategorySpec] = Field(default_factory=list)
    output_shape: Literal["object", "list"] = "object"
    output: OutputConfig = Field(default_factory=OutputConfig)
    # —— v1 fields (unchanged) ——
    system_prompt: str = ""              # compiler fills if empty
    fields: list[FieldSpec] = Field(default_factory=list)
    few_shot: list[FewShot] = Field(default_factory=list)
    validators: list[Validator] = Field(default_factory=list)
    review_policy: ReviewPolicy = ReviewPolicy()
    model_binding: ModelBinding = ModelBinding()
    parser: str | None = None            # pin a parser; None = auto route (§4.3)
    additional_rules: str = ""


class SkillPackage(_SkillPackageCore):
    """STRICT shape: writes (save/publish/import) reject unknown fields and
    schema_version > 2 (§3.3 — never silently drop new fields)."""
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _reject_future_schema(self):
        if self.schema_version > 2:
            raise ValueError(
                f"schema_version {self.schema_version} 高于本环境支持的版本 2，"
                "请升级服务端后再导入")
        return self


class SkillPackageLoose(_SkillPackageCore):
    """READ path for stored rows: tolerates keys the current model does not
    know yet AND future schema_version values (forward compatibility) —
    unlike the strict write path."""
    model_config = ConfigDict(extra="ignore")