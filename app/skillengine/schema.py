"""SkillPackage — the compiled, versioned contract of a skill (design v0.2 §5.1).
"Sample-as-model": 1-2 samples become few-shot pairs + anchor hints; extraction
relies on prompt engineering + JSON schema hard constraint, never training.
Field dual mode (§5.6): verbatim values must be locatable in source text;
inferred values (classification/derivation/suggestion) must carry reasoning.
"""
from pydantic import BaseModel, Field


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


class SkillPackage(BaseModel):
    skill_code: str
    name: str = ""
    description: str = ""                # editor "描述" (human context, not prompt)
    kind: str = "extract"                # extract|audit (§5.5, audit lands M3)
    doc_type_hint: str = ""
    system_prompt: str = ""              # compiler fills if empty
    fields: list[FieldSpec] = Field(default_factory=list)
    few_shot: list[FewShot] = Field(default_factory=list)
    validators: list[Validator] = Field(default_factory=list)
    review_policy: ReviewPolicy = ReviewPolicy()
    model_binding: ModelBinding = ModelBinding()
    parser: str | None = None            # pin a parser; None = auto route (§4.3)
    additional_rules: str = ""
