"""PR-B: image-first extraction is a single trusted request, with honest source metadata."""
import hashlib
import json

import httpx
import pytest

from app.config import get_settings
from app.extraction import custom_providers
from app.extraction.vision_first import extract_from_images, vision_provider
from app.parsers.base import Block, Page, UDR
from app.skillengine.compiler import compile_messages
from app.skillengine.schema import FieldSpec, SkillPackage
from app.tenancy import as_tenant

TENANT = "vision-qa"
IMAGE = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
         "AAAAC0lEQVR42mP8/x8AAwMCAO+jv2sAAAAASUVORK5CYII=")
VALUES = {"seller": "Paper Co.", "items": [{"unit": "吨", "quantity": "2", "tax_rate": "免税", "tax": "***"}]}


@pytest.fixture
def channels(monkeypatch):
    # No provider credentials: isolate this tenant's warmed cache and use MockTransport exclusively.
    common = {"base_url": "https://fixture.invalid/v1", "api_key": "", "no_key": True, "extra_body": {}}
    monkeypatch.setitem(custom_providers._cache, TENANT, {
        "camera": {**common, "model": "camera-model", "vision": True},
        "text": {**common, "model": "text-model", "vision": False},
    })
    monkeypatch.setenv("IDP_FAST_VISION_PROVIDER", "")
    monkeypatch.setenv("IDP_VISION_FIRST_PROVIDER", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def skill(channel="camera", **settings):
    return SkillPackage(
        skill_code="image_invoice", vision_first=True,
        fields=[{"name": "seller"}, {"name": "items", "type": "table", "columns": [
            {"name": name} for name in ("unit", "quantity", "tax_rate", "tax")]}],
        model_binding={"extractor": channel, "fallback": "text", "challenger": "text"},
        **settings)


def parsed(page_count=1):
    # The scan's printed amount and tax markers are absent from its parser text.
    return UDR(parser="glm-ocr-cloud", pages=[
        Page(page_no=i + 1, blocks=[Block(text="纸")]) for i in range(page_count)],
        full_markdown="\n\n".join(["纸"] * page_count))


def transport_for(calls, status=200, payload=VALUES):
    def serve(req):
        body = json.loads(req.content)
        calls.append(body)
        if status != 200:
            return httpx.Response(status, json={"error": "fixture provider unavailable"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}],
                                         "usage": {"prompt_tokens": 17, "completion_tokens": 9}})
    return httpx.MockTransport(serve)


def test_only_trusted_image_channels_are_selected(channels, monkeypatch):
    assert vision_provider(skill(), TENANT) == "camera"
    assert vision_provider(skill("qwen", parser="vlm-qwen"), TENANT) == "vision-qwen"
    # Names and text-only declarations do not establish the ability to process an image.
    monkeypatch.setitem(custom_providers._cache, TENANT, {
        "vision-sounding": {"model": "vision-sounding", "vision": False}})
    assert vision_provider(skill("vision-sounding"), TENANT) is None
    assert vision_provider(skill("qwen"), TENANT) is None
    assert vision_provider(skill(), "tenant-without-camera") is None


@pytest.mark.parametrize("pages", [1, 2])
def test_image_request_retains_tax_strings_no_text_fallback_or_challenger(channels, pages):
    calls = []
    images = dict.fromkeys(range(1, pages + 1), IMAGE)
    with as_tenant(TENANT):
        result, usage, _ = extract_from_images(parsed(pages), skill(), images, TENANT,
                                               transport=transport_for(calls))
    assert len(calls) == 1
    body = calls[0]
    assert body["model"] == "camera-model"
    parts = body["messages"][-1]["content"]
    assert [p["image_url"]["url"] for p in parts if p["type"] == "image_url"] == list(images.values())
    assert usage["prompt_tokens"] == 17 and usage["completion_tokens"] == 9
    assert result["seller"]["$value"] == "Paper Co."
    assert result["seller"]["$source"] == "vision"
    assert not result["seller"]["$bbox"]
    row, = result["items"]
    for key, value in VALUES["items"][0].items():
        assert row[key] == value
        assert row["$cells"][key]["$source"] == "vision"
        assert not row["$cells"][key].get("$hits")


@pytest.mark.parametrize("failure", ["no_provider", "no_image", "partial_image", "provider_error"])
def test_unavailable_image_extraction_returns_review_without_text_retry(channels, monkeypatch, failure):
    calls = []
    images = {} if failure == "no_image" else {1: IMAGE}
    if failure == "no_provider":
        monkeypatch.setitem(custom_providers._cache, TENANT, {})
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            parsed(2 if failure == "partial_image" else 1), skill(), images, TENANT,
            transport=transport_for(calls, status=503))

    assert len(calls) == (1 if failure == "provider_error" else 0)
    assert review and usage["review_reasons"]
    assert result["seller"]["$value"] == "" and result["items"] == []
    assert result["seller"].get("$source") != "vision"


def test_list_source_tags_do_not_treat_record_outline_as_a_detail_table(channels):
    document = UDR(parser="glm-ocr-cloud", pages=[
        Page(page_no=1, width=100, height=100, blocks=[
            Block(text="Paper Co.", bbox=[1, 2, 30, 14])])])
    calls = []
    with as_tenant(TENANT):
        result, _, _ = extract_from_images(
            document, skill(output_shape="list"), {1: IMAGE}, TENANT,
            transport=transport_for(calls, payload={"records": [VALUES]}))
    assert len(calls) == 1
    record, = result["records"]
    assert record["$record_bbox"] == [{"page": 1, "bbox": [1, 2, 30, 14]}]
    assert record["seller"]["$source"] == "vision"
    assert record["items"][0]["$cells"]["tax_rate"]["$source"] == "vision"


def test_opt_out_keeps_the_preexisting_object_prompt_byte_identical():
    # Snapshot from the same skill and UDR compiled on main at 98fff53, before PR-A/B.
    old_shape = skill().model_copy(update={"vision_first": False})
    text = json.dumps(compile_messages(old_shape, parsed()), ensure_ascii=False).encode()
    assert hashlib.sha256(text).hexdigest() == "239d8df6154c9aeb3802e7b54e7e9fe50b670b30717745a9a789b6b98b69f380"


def test_long_object_batches_present_every_original_image_once(channels):
    calls = []
    # The provider limit is eight images per request; page identity must survive batching.
    images = {page: IMAGE.replace("image/png;", f"image/png;page={page};") for page in range(1, 10)}
    with as_tenant(TENANT):
        result, usage, _ = extract_from_images(
            parsed(9), skill(), images, TENANT, transport=transport_for(calls))
    batches = [[part["image_url"]["url"] for part in body["messages"][-1]["content"]
                if part["type"] == "image_url"] for body in calls]
    assert [len(batch) for batch in batches] == [8, 1]
    assert batches[0] + batches[1] == list(images.values())
    assert len(result["items"]) == 2 and result["seller"]["$value"] == "Paper Co."
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (34, 18)


async def test_runner_flags_structural_gap_after_one_image_pass_and_meters_it(channels, tmp_path, monkeypatch):
    from functools import partial

    from app.extraction import vision_first
    from app.tasks import runner
    from tests.test_advanced_runner import Env

    # The model can see the missing data in the image. The damaged parser evidence
    # still merits human review, never an automatic second extraction.
    invoice = skill(review_policy={"mode": "never"})
    source = parsed()
    html = ("<table><thead><tr><th>unit</th><th>quantity</th><th>tax_rate</th><th>tax</th>"
            "</tr></thead><tbody><tr><td colspan='4'>吨</td></tr></tbody></table>")
    source.pages[0].blocks = [Block(type="table", text=html)]
    source.full_markdown = html
    monkeypatch.setitem(custom_providers._cache, "default", custom_providers._cache[TENANT])
    monkeypatch.setattr(custom_providers, "_warmed", {"default"})
    calls, meter = [], []
    monkeypatch.setattr(vision_first, "extract_from_images",
                        partial(vision_first.extract_from_images, transport=transport_for(calls)))
    monkeypatch.setattr(runner, "_vision_first_images", lambda *args: {1: IMAGE})

    async def capture_usage(**entry):
        meter.append(entry)

    def disallow_text_pass(*args, **kwargs):
        pytest.fail("Runner invoked another extraction pass")

    monkeypatch.setattr(runner, "shadow_meter", capture_usage)
    async with Env(tmp_path, monkeypatch, invoice) as env:
        _, files, _, _ = await env.seed_and_run(
            monkeypatch, parse_fake=lambda *args, **kwargs: source, extract_fake=disallow_text_pass)
        file = files["bundle.pdf"]
        assert len(calls) == 1 and len(meter) == 1
        assert file.status == "pending_verification"
        assert file.document_meta["review_reasons"] == ["parse_cells_collapsed"]
        assert file.document_meta["metrics"]["vision_first"] == {"attempted": True, "provider": "camera"}
        assert (file.input_tokens, file.output_tokens) == (17, 9)
        assert (meter[0]["usage"]["prompt_tokens"], meter[0]["usage"]["completion_tokens"]) == (17, 9)
        assert file.result["items"][0]["tax_rate"] == "免税"


@pytest.mark.parametrize(("price", "amount", "flag"), [
    ("3", "9", True), ("3", "6", False), ("3", "6.01", False), ("免税", "9", False),
    ("3", "", False), ("NaN", "9", False),
])
def test_configured_row_product_only_flags_numeric_disagreements(channels, price, amount, flag):
    invoice = skill(validators=[{"type": "row_product", "field": "items", "target": "amount",
                                 "parts": ["quantity", "price"]}], review_policy={"mode": "never"})
    invoice.fields[1].columns.extend([FieldSpec(name="price"), FieldSpec(name="amount")])
    payload = {"seller": "Paper Co.", "items": [{**VALUES["items"][0], "price": price, "amount": amount}]}
    calls = []
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            parsed(), invoice, {1: IMAGE}, TENANT, transport=transport_for(calls, payload=payload))
    assert len(calls) == 1
    assert ("table_arithmetic_mismatch" in usage.get("review_reasons", [])) is flag
    assert review is flag
    row, = result["items"]
    assert (row["amount"], row["price"], row["tax_rate"], row["tax"]) == (amount, price, "免税", "***")
    amount_meta = row["$cells"]["amount"]
    assert bool(amount_meta.get("$rule_failures")) is flag
    if flag:
        assert amount_meta["$confidence"] <= 1
