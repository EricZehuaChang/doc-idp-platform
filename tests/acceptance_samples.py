"""M1 acceptance: run a generic-invoice skill over representative files from
the three 中投 sample groups (the acceptance baseline named in design v0.2 §10).
Real GLM-OCR + qwen3.7-plus — prints per-file field/confidence table.
Usage: .venv/Scripts/python tests/acceptance_samples.py
"""
import io
import sys
import time

sys.path.insert(0, r"D:/Claude Code/env")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from load_keys import load_keys  # noqa: E402

load_keys()

from app.extraction.pipeline import extract          # noqa: E402
from app.parsers.router import parse_document        # noqa: E402
from app.skillengine.schema import (                 # noqa: E402
    FieldSpec, ModelBinding, ReviewPolicy, SkillPackage, Validator)

BASE = r"D:\中投创展\Insavlo\测试用sample"
SAMPLES = [
    # group 1: Generic Invoice 3.0 (multi-language invoices)
    (rf"{BASE}\skill-Generic Invoice 3.0\101590.pdf", "巴西葡语 NF-e 电子发票"),
    (rf"{BASE}\skill-Generic Invoice 3.0\两张发票在同一页.png", "一页双票（难例）"),
    (rf"{BASE}\skill-Generic Invoice 3.0\微信图片_20260319112622_14_482.png", "阿语 Uber 小票截图"),
    # group 2: Mubea custom invoices
    (rf"{BASE}\skill-Invoice_Mubea_Custom (Copy-测试用)\Mubea China TC INV#02.pdf", "Mubea 中国发票"),
    (rf"{BASE}\skill-Invoice_Mubea_Custom (Copy-测试用)\China SY inv 3.pdf", "China SY 发票"),
    # group 3: fuzzy collection
    (rf"{BASE}\模糊demo合集-skill用Generic Document to Markdown\电子发票.png", "中文电子发票"),
]

pkg = SkillPackage(
    skill_code="acceptance_invoice", name="验收-通用发票",
    doc_type_hint="各类发票/小票（多语言）",
    fields=[
        FieldSpec(name="invoice_no", instruction="发票号码/单号", required=True),
        FieldSpec(name="invoice_date", instruction="开票日期，保持原文格式"),
        FieldSpec(name="total_amount", type="number", instruction="价税合计/总金额"),
        FieldSpec(name="currency", mode="inferred", type="enum",
                  enum_values=["CNY", "USD", "EUR", "BRL", "SAR", "JPY", "其他", "未知"],
                  instruction="判断币种"),
        FieldSpec(name="seller", instruction="开票方/商家名称"),
    ],
    validators=[Validator(type="required", field="invoice_no")],
    review_policy=ReviewPolicy(mode="auto", confidence_threshold=2),
    model_binding=ModelBinding(extractor="qwen"),
)

rows = []
for path, label in SAMPLES:
    t0 = time.time()
    try:
        udr = parse_document(path)
        result, usage, needs_review = extract(udr, pkg)
        cells = {k: v for k, v in result.items() if isinstance(v, dict)}
        confs = {k: v.get("$confidence") for k, v in cells.items()}
        filled = sum(1 for v in cells.values() if v.get("$value"))
        rows.append((label, udr.parser, f"{time.time()-t0:.0f}s",
                     f"{filled}/{len(cells)}", str(confs),
                     "REVIEW" if needs_review else "PASS",
                     {k: str(v.get('$value'))[:28] for k, v in cells.items()}))
    except Exception as e:
        rows.append((label, "-", f"{time.time()-t0:.0f}s", "-", "-",
                     f"ERROR: {str(e)[:70]}", {}))

print(f"\n{'样本':<18}{'解析器':<14}{'耗时':<6}{'填充':<7}{'判定':<8}")
print("-" * 100)
for label, parser, dur, filled, confs, verdict, values in rows:
    print(f"{label:<18}{parser:<14}{dur:<6}{filled:<7}{verdict:<8}")
    if values:
        print(f"   值: {values}")
        print(f"   置信: {confs}")
