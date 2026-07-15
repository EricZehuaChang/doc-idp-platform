"""Real-world canary (run manually, burns a few thousand tokens):
real sample PDF -> GLM-OCR cloud parse -> qwen3.7-plus extraction -> result.
Usage:  .venv/Scripts/python tests/canary_real.py
Keys are injected from D:/Claude Code/env/secrets.yaml (local dev convention).
"""
import io
import json
import sys

sys.path.insert(0, r"D:/Claude Code/env")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from load_keys import load_keys  # noqa: E402

load_keys()

from app.extraction.pipeline import extract          # noqa: E402
from app.parsers.router import parse_document        # noqa: E402
from app.skillengine.schema import (                 # noqa: E402
    FieldSpec, ModelBinding, SkillPackage, Validator)

SAMPLE = r"D:\中投创展\Insavlo\测试用sample\skill-Generic Invoice 3.0\101590.pdf"

pkg = SkillPackage(
    skill_code="canary_invoice", name="金丝雀-通用发票",
    doc_type_hint="发票/NF-e",
    fields=[
        FieldSpec(name="invoice_no", instruction="发票号码/NF-e编号", required=True),
        FieldSpec(name="issuer", instruction="开票方公司名"),
        FieldSpec(name="buyer", instruction="购买方公司名"),
        FieldSpec(name="tax_id", instruction="税号（如 CNPJ）"),
        FieldSpec(name="doc_language", mode="inferred", type="enum",
                  enum_values=["中文", "英语", "葡萄牙语", "其他"],
                  instruction="判断票面主要语言"),
    ],
    validators=[Validator(type="required", field="invoice_no")],
    model_binding=ModelBinding(extractor="qwen"),
)

print(f"[1/2] parsing via auto-route: {SAMPLE}")
udr = parse_document(SAMPLE)
print(f"      parser={udr.parser} pages={len(udr.pages)} md_len={len(udr.full_markdown)}")

print("[2/2] extracting via qwen3.7-plus ...")
result, usage, needs_review = extract(udr, pkg)
print(f"      usage={usage} needs_review={needs_review}")
print(json.dumps(result, ensure_ascii=False, indent=1)[:1500])
