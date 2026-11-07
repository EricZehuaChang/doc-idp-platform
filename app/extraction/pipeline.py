"""Extraction pipeline (design v0.2 §5.2): UDR + SkillPackage -> result dict in
the live-verified schema {field: {"$value","$confidence","$bbox","$pages"}} with
tables as row arrays and inferred fields carrying "$reasoning"+"inferred".
Pure function of (udr, pkg) — idempotent by design (HA discipline).
"""
import re

from app.config import load_providers
from app.extraction import confidence as conf
from app.extraction import formatting
from app.extraction.list_chunks import (append_chunk, boundary_messages, invalid_structure,
                                        list_units, model_records)
from app.extraction.record_geometry import annotate_record_regions
from app.extraction.provider_client import chat_json_with_fallback
from app.extraction.validators import run_validators
from app.parsers.base import UDR
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import SkillPackage


def _norm(v: str) -> str:
    """Value comparison for arbitration: whitespace/case/thousand-separator
    insensitive — two models phrasing '1,026.50' vs '1026.50' still agree."""
    return re.sub(r"[\s,，]", "", v).lower()


def _provider_chain(pkg: SkillPackage, override: str | None = None) -> list[str | None]:
    """Resolve the effective failover chain.

    An explicit skill binding stays authoritative.  An unbound skill inherits
    the platform chain declared in providers.yaml; this is the documented
    meaning of an empty extractor and prevents a transient active-channel
    failure from turning directly into a file error.
    """
    if override:
        return [override]
    extractor = pkg.model_binding.extractor
    chain: list[str | None] = [extractor or None]
    if pkg.model_binding.fallback:
        chain.append(pkg.model_binding.fallback)
    elif not extractor:
        chain.extend(load_providers().get("fallback", []))
    # Config mistakes such as repeating the active provider in fallback should
    # not cause duplicate paid calls.
    return list(dict.fromkeys(chain))


def _page_udr(udr: UDR, page) -> UDR:
    markdown = page.markdown or "\n".join(b.text for b in page.blocks if b.text)
    return UDR(pages=[page], full_markdown=markdown, parser=udr.parser, lang=udr.lang)


def _has_value(value: object, inferred: bool = False) -> bool:
    if inferred and isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return value != []


def _merge_page_raw(parts: list[dict], pkg: SkillPackage) -> dict:
    """Reduce page-level model output back to the skill contract.

    Detail rows retain page order.  Scalars use the last non-empty value: page
    headers repeat unchanged while invoice totals/remarks commonly appear only
    on the final page.  Empty later pages therefore cannot erase an earlier
    value, while end-of-document summaries correctly win over page subtotals.
    """
    merged: dict[str, object] = {}
    for field in pkg.fields:
        if field.type == "table":
            rows: list = []
            for part in parts:
                value = part.get(field.name)
                if isinstance(value, list):
                    rows.extend(value)
            merged[field.name] = rows
            continue
        chosen: object = ""
        for part in parts:
            if field.name not in part:
                continue
            candidate = part[field.name]
            if _has_value(candidate, inferred=field.mode == "inferred"):
                chosen = candidate
        merged[field.name] = chosen
    return merged


# A vision call carries a full-page raster per image; more than a handful in
# one request blows past context limits and costs far more than it explains.
_MAX_VISION_IMAGES = 8


def _images_for(unit: UDR, page_images: dict[int, str] | None) -> list[str]:
    """Page rasters belonging to this extraction unit, in page order."""
    if not page_images:
        return []
    nos = [p.page_no for p in unit.pages]
    return [page_images[n] for n in nos if n in page_images][:_MAX_VISION_IMAGES]


def _raw_extract(udr: UDR, pkg: SkillPackage, chain: list[str | None],
                 transport=None,
                 page_images: dict[int, str] | None = None,
                 single_call: bool = False
                 ) -> tuple[dict, dict, list[str]]:
    """Model call with page-map/table-reduce for long multi-page outputs.

    `page_images` (page_no -> data URI) turns this into a multimodal call: the
    caller decides, because only it knows whether the bound channel accepts
    images. Text-only channels must never receive image parts. A4: List chunks
    only when necessary and merges explicit continuations across boundaries."""
    # Entity-list tables are document-wide de-duplicated sweeps, not layout
    # detail tables.  Keep them whole so page boundaries do not duplicate hits.
    page_map = pkg.output_shape != "list" and not single_call and len(udr.pages) > 1 and any(
        f.type == "table" and not f.entity_list for f in pkg.fields)
    list_mode = pkg.output_shape == "list"
    image_first = single_call and bool(page_images) and not list_mode
    if list_mode or image_first:
        # B2: image-first Object batches obey the same lossless text/raster limits.
        units = list_units(udr, page_images or {}, _MAX_VISION_IMAGES)
    else:
        pages = [_page_udr(udr, page) for page in udr.pages] if page_map else [udr]
        units = [(unit, _images_for(unit, page_images)) for unit in pages]
    chunked_list = list_mode and len(units) > 1
    parts: list[dict] = []
    records: list[dict] = []
    chunk_reasons = []
    total_usage: dict[str, object] = {}
    providers: list[str] = []
    for unit, images in units:
        messages = compile_messages(pkg, unit, images)
        if chunked_list:
            boundary_messages(messages, records[-1] if records else None, pkg.fields)
        raw, usage, used = chat_json_with_fallback(messages, chain, transport=transport)
        if chunked_list:
            if invalid_structure(raw, pkg.fields, complete=False):
                chunk_reasons.append("records_invalid")
            if append_chunk(records, raw, pkg.fields):
                chunk_reasons.append("records_boundary_uncertain")
        else:
            parts.append(raw)
        if used not in providers:
            providers.append(used)
        for key, value in dict(usage).items():
            if isinstance(value, (int, float)):
                total_usage[key] = total_usage.get(key, 0) + value
            elif len(units) == 1:
                total_usage[key] = value
    total_usage["provider_used"] = ",".join(providers)
    if chunked_list:
        if chunk_reasons:
            total_usage["review_reasons"] = list(dict.fromkeys(chunk_reasons))
        return {"records": records}, total_usage, providers
    merged = _merge_page_raw(parts, pkg) if page_map or (image_first and len(parts) > 1) else parts[0]
    return merged, total_usage, providers


def _percent_hits(hits: list, udr: UDR) -> list:
    """Enrich each hit with page-percent x/y/w/h (top-left origin, 0-100).

    /status carries no page dims, so a masking consumer (mask-guard route-A)
    cannot convert parser-space bbox itself — and the parser space varies
    (pdfplumber = PDF points, OCR = render pixels). Converting here, where the
    UDR is in hand, is the only place both sides of the ratio are known.
    Zero-dim pages (markitdown has no geometry) keep the bare {page, bbox}
    contract: emitting percent against a fake size would be a wrong box, and a
    wrong box gets masked as if it were right."""
    out = []
    for h in hits:
        entry = dict(h)
        bbox, page_no = h.get("bbox"), h.get("page")
        if (bbox and len(bbox) == 4 and isinstance(page_no, int)
                and 1 <= page_no <= len(udr.pages)):
            p = udr.pages[page_no - 1]
            if p.width and p.height:
                x0, top, x1, bottom = bbox
                entry.update(
                    x=round(x0 / p.width * 100, 2),
                    y=round(top / p.height * 100, 2),
                    w=round((x1 - x0) / p.width * 100, 2),
                    h=round((bottom - top) / p.height * 100, 2))
        out.append(entry)
    return out


def _locate_rows(rows: list, spec, udr: UDR) -> list:
    """Attach per-cell source locations to table rows as $-prefixed metadata
    (mirroring the scalar cell contract): row["$cells"][col] =
    {"$confidence", "$hits" [{page, bbox}...]}. Rows keep their plain column
    values — the review grid and rows PATCH are untouched; masking consumers
    read every occurrence from $hits. Inferred columns are derivations
    (nothing to locate); empty cells carry no metadata. Cell scores never
    feed the needs_review gate — masking skills enforce human review via
    review_policy.mode="always" instead (§5.6 discipline stays scalar-only)."""
    out = []
    for row in rows:
        if isinstance(row, dict):
            cells = {}
            for c in spec.columns:
                if c.mode == "inferred" or c.type == "table":
                    continue
                v = row.get(c.name)
                if v is None or isinstance(v, (dict, list)):
                    continue
                sval = str(v).strip()
                if not sval:
                    continue
                score, hits = conf.score_cell(sval, udr)
                cells[c.name] = {"$confidence": score,
                                 "$hits": _percent_hits(hits, udr)}
            if cells:
                row = {**row, "$cells": cells}
        out.append(row)
    return out


def _record_failures(raw: dict, pkg: SkillPackage) -> dict:
    # flatten for the rule channel: inferred fields arrive as {value, reasoning}
    flat: dict[str, object] = {}
    for f in pkg.fields:
        v = raw.get(f.name)
        if f.mode == "inferred" and isinstance(v, dict):
            flat[f.name] = v.get("value")
        else:
            flat[f.name] = v
    return run_validators(flat, pkg.validators)


def _build_cells(raw: dict, pkg: SkillPackage, udr: UDR, rule_failures: dict,
                 challenger_raw: dict | None, fast: bool) -> tuple[dict, int]:
    """Build scalar metadata and located table rows identically for a document or one record."""
    result: dict[str, object] = {}
    lowest = 3
    for f in pkg.fields:
        raw_val = raw.get(f.name)
        if f.type == "table":
            rows = raw_val if isinstance(raw_val, list) else []
            if pkg.output_shape == "list":
                rows = [{k: v for k, v in row.items() if not k.startswith("$")}
                        for row in rows if isinstance(row, dict)]
            if not fast:
                rows = _locate_rows(rows, f, udr)   # fast mode: no bbox scoring
            result[f.name] = formatting.format_table_rows(rows, f)
            continue
        reasoning = None
        if f.mode == "inferred" and isinstance(raw_val, dict):
            value = str(raw_val.get("value") or "")
            reasoning = str(raw_val.get("reasoning") or "")
        else:
            value = "" if raw_val is None else str(raw_val)
        if fast:
            # 未评分契约 (§WP5): confidence None — readers must render "未评分";
            # fabricating a number would fake quality the pipeline never measured
            cell: dict[str, object] = {"$value": value, "$confidence": None,
                                       "$bbox": [], "$pages": ""}
            if f.mode == "inferred":
                cell["inferred"] = True
                cell["$reasoning"] = reasoning or ""
            if f.name in rule_failures:
                cell["$rule_failures"] = rule_failures[f.name]
            formatted, fmt_err = formatting.format_value(f, value)
            if fmt_err is None:
                if formatted != value:
                    cell["$raw"] = value
                    cell["$value"] = formatted
            else:
                cell["$format_error"] = fmt_err
            result[f.name] = cell
            continue
        score, page, bbox = conf.score_field(
            value=value, udr=udr,
            rule_failed=f.name in rule_failures,
            inferred=(f.mode == "inferred"),
            has_reasoning=bool(reasoning))
        cell = {
            "$value": value, "$confidence": score,
            "$bbox": bbox or [], "$pages": page or "",
        }
        # model channel (§5.3): challenger disagreement caps confidence at 1
        # (below any sane threshold) and records the second opinion
        if challenger_raw is not None and f.name in challenger_raw:
            ch_val = challenger_raw.get(f.name)
            if f.mode == "inferred" and isinstance(ch_val, dict):
                ch_val = ch_val.get("value")
            ch_str = "" if ch_val is None else str(ch_val)
            agree = _norm(ch_str) == _norm(value)
            if not agree:
                score = min(score, 1)
                cell["$confidence"] = score
            cell["$challenger"] = {"value": ch_str, "agree": agree}
        if f.mode == "inferred":
            cell["inferred"] = True        # exports must distinguish extracted vs derived (§5.6)
            cell["$reasoning"] = reasoning or ""
        if f.name in rule_failures:
            cell["$rule_failures"] = rule_failures[f.name]
        # 9.15 R11: format AFTER locating/scoring the raw value — formatting
        # first would break `$bbox`/highlight lookup. Success adds `$raw`; a
        # parse failure keeps the raw value, caps confidence at 1 (auto-review)
        # and never fabricates a value.
        formatted, fmt_err = formatting.format_value(f, value)
        if fmt_err is None:
            if formatted != value:
                cell["$raw"] = value
                cell["$value"] = formatted
        else:
            cell["$format_error"] = fmt_err
            score = min(score, 1)
            cell["$confidence"] = score
        result[f.name] = cell
        lowest = min(lowest, score)

    return result, lowest


def _build_records(raw: dict, pkg: SkillPackage, udr: UDR, challenger_raw: dict | None,
                   usage: dict, fast: bool) -> tuple[dict, int]:
    """A4: preserve record order, arbitrate by index, and aggregate review across records."""
    rows = model_records(raw)
    challenger = model_records(challenger_raw) if challenger_raw is not None else None
    mismatch = challenger is not None and len(challenger) != len(rows)
    reasons = list(usage.get("review_reasons") or [])
    if not rows:
        reasons.append("records_empty")
    if invalid_structure(raw, pkg.fields, complete=True) and "records_invalid" not in reasons:
        reasons.append("records_invalid")
    if mismatch:
        reasons.append("challenger_record_count_mismatch")
        usage["challenger_record_count_mismatch"] = True

    records = []
    lowest = 3
    for i, row in enumerate(rows):
        opinion = None
        if challenger is not None and i < len(challenger):
            opinion = {f.name: challenger[i].get(f.name) for f in pkg.fields}
        cells, score = _build_cells(row, pkg, udr, _record_failures(row, pkg), opinion, fast)
        if mismatch:
            score = min(score, 1)
            for value in cells.values():
                if isinstance(value, dict):
                    if value.get("$confidence") is not None:
                        value["$confidence"] = min(value["$confidence"], 1)
                else:
                    for detail in value:
                        for cell in detail.get("$cells", {}).values():
                            if cell.get("$confidence") is not None:
                                cell["$confidence"] = min(cell["$confidence"], 1)
        lowest = min(lowest, score)
        records.append(cells)
    if annotate_record_regions(records):
        reasons.append("records_overlap")
    usage["review_reasons"] = reasons
    return {"records": records}, lowest


def extract(udr: UDR, pkg: SkillPackage, transport=None,
            provider_override: str | None = None,
            page_images: dict[int, str] | None = None,
            fast: bool = False, single_call: bool = False) -> tuple[dict, dict, bool]:
    """Returns (result, usage, needs_review). Resilience: extractor -> fallback
    chain with cooldown (M1 acceptance hit exactly this failure mode).
    Challenger arbitration (§5.3 model channel): a second model re-extracts and
    disagreements are forced into human review.

    `fast=True` (9.15 WP5 极速模式): one single model call for the whole file
    (no per-page split & merge), no challenger, no cell scoring — the result
    contract carries $confidence=None/$bbox=[]/$pages="" ("未评分", never a
    fabricated high score); rule failures still surface as $rule_failures."""
    chain = _provider_chain(pkg, provider_override)
    rules_first = pkg.extraction_channel == "rules_first"
    if rules_first and pkg.output_shape == "list":
        raise ValueError("List 输出模式不支持规则优先抽取，请选择模型抽取")
    rule_values: dict = {}
    review_misses: set[str] = set()
    model_pkg = pkg
    if rules_first:
        from app.extraction.rules import extract_rules
        from app.extraction.validators import _to_number
        rule_values = extract_rules(udr, pkg.fields)
        failures = run_validators(rule_values, pkg.validators)
        for field in pkg.fields:
            value = rule_values.get(field.name)
            if field.name in failures or (value is not None and (
                    (field.type == "number" and _to_number(value) is None)
                    or (field.type == "enum" and field.enum_values and value not in field.enum_values)
                    or formatting.format_value(field, str(value))[1] is not None)):
                rule_values.pop(field.name, None)
        # With no parser text (fast vision), every field retains the existing
        # model path regardless of a textual rule's on_miss choice.
        has_text = bool((udr.full_markdown or udr.full_text()).strip())
        review_misses = {f.name for f in pkg.fields if has_text and f.rule
                         and f.rule.on_miss == "review" and f.mode != "inferred"
                         and f.name not in rule_values}
        model_fields = [f for f in pkg.fields if f.name not in rule_values and f.name not in review_misses]
        model_pkg = pkg.model_copy(update={"fields": model_fields,
            "few_shot": [sample.model_copy(update={"expected_output": {
                k: v for k, v in sample.expected_output.items() if k in {f.name for f in model_fields}}})
                for sample in pkg.few_shot]})
    if model_pkg.fields:
        raw, usage, providers_used = _raw_extract(
            udr, model_pkg, chain, transport=transport, page_images=page_images,
            single_call=fast or single_call)
        if rules_first:
            raw = {f.name: raw.get(f.name) for f in model_pkg.fields}
    else:
        raw, usage, providers_used = {}, {"prompt_tokens": 0, "completion_tokens": 0}, []
    raw = {**raw, **rule_values}
    model_names = {f.name for f in model_pkg.fields}
    if rules_first:
        usage.update(rule_fields=len(rule_values), model_fields=len(model_names),
                     model_called=bool(model_names), review_misses=sorted(review_misses),
                     field_sources={f.name: ("rule" if f.name in rule_values else
                         "model" if f.name in model_names else "review") for f in pkg.fields})

    # challenger pass (skipped for dry-run overrides: they compare providers
    # explicitly). Best-effort: an unavailable challenger never fails the file.
    challenger_raw: dict | None = None
    challenger = None if fast else pkg.model_binding.challenger
    if model_pkg.fields and challenger and not provider_override and challenger not in providers_used:
        try:
            challenger_raw, ch_usage, _ = _raw_extract(
                udr, model_pkg, [challenger], transport=transport)
            if pkg.output_shape == "list" and ch_usage.get("review_reasons"):
                usage["review_reasons"] = list(dict.fromkeys(
                    [*usage.get("review_reasons", []), *ch_usage["review_reasons"]]))
            usage["challenger_used"] = challenger
            usage["challenger_prompt_tokens"] = int(ch_usage.get("prompt_tokens") or 0)
            usage["challenger_completion_tokens"] = int(ch_usage.get("completion_tokens") or 0)
        except Exception as e:      # arbitration degraded, primary result stands
            usage["challenger_error"] = str(e)[:200]

    if pkg.output_shape == "list":
        result, lowest = _build_records(raw, pkg, udr, challenger_raw, usage, fast)
    else:
        if challenger_raw is not None:
            challenger_raw = {name: challenger_raw.get(name) for name in model_names}
        result, lowest = _build_cells(raw, pkg, udr, _record_failures(raw, pkg),
                                      challenger_raw, fast)

    # C2/C3: only unspecified-column tables enter header provenance/shape checks.
    from app.extraction.table_headers import resolve_table_headers
    free_tables = [field for field in pkg.fields if field.type == "table" and not field.columns
                   and not field.entity_list]
    records = result.get("records", []) if pkg.output_shape == "list" else [result]
    headers = {}
    header_reasons = []
    for index, record in enumerate(records):
        for field in free_tables:
            rows, meta, reasons = resolve_table_headers(
                field, record.get(field.name) or [], udr,
                sole_table=sum(field.type == "table" for field in pkg.fields) == 1 and len(records) == 1)
            record[field.name] = rows
            path = f"records[{index}].{field.name}" if pkg.output_shape == "list" else field.name
            headers[path] = meta
            header_reasons.extend(reasons)
    if headers:
        usage["table_headers"] = headers
    if header_reasons:
        usage["review_reasons"] = list(dict.fromkeys([
            *usage.get("review_reasons", []), *header_reasons]))

    policy = pkg.review_policy
    if policy.mode == "always":
        needs_review = True
    elif policy.mode == "never":
        needs_review = False
    else:
        needs_review = lowest < policy.confidence_threshold
    if rules_first:
        for name, value in result.items():
            source = usage["field_sources"][name]
            if isinstance(value, dict):
                value["$source"] = source
            elif isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        for column in next(f.columns for f in pkg.fields if f.name == name):
                            row.setdefault("$cells", {}).setdefault(column.name, {})["$source"] = source
        if review_misses:
            needs_review = True
    return result, usage, needs_review or bool(usage.get("review_reasons"))
