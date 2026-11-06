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
        assert set(file.document_meta["review_reasons"]) == {"vision_value_unverified", "parse_cells_collapsed"}
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
    document = parsed()
    # Isolate the arithmetic rule: the parser saw these raw values, even the erroneous printed amount.
    document.full_markdown = " ".join([payload["seller"], *payload["items"][0].values()])
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            document, invoice, {1: IMAGE}, TENANT, transport=transport_for(calls, payload=payload))
    assert len(calls) == 1
    assert ("table_arithmetic_mismatch" in usage.get("review_reasons", [])) is flag
    assert review is flag
    row, = result["items"]
    assert (row["amount"], row["price"], row["tax_rate"], row["tax"]) == (amount, price, "免税", "***")
    amount_meta = row["$cells"]["amount"]
    assert bool(amount_meta.get("$rule_failures")) is flag
    if flag:
        assert amount_meta["$confidence"] <= 1


@pytest.mark.parametrize("shape", ["object", "list"])
def test_missing_parser_values_require_review_even_with_verified_header_and_complete_rows(channels, shape):
    invoice = skill(output_shape=shape, review_policy={"mode": "never"})
    document = UDR(parser="glm-ocr-cloud", full_markdown="Paper Co.",
                   pages=[Page(page_no=1, blocks=[Block(text="Paper Co.", bbox=[1, 1, 30, 10])])])
    payload = {"records": [VALUES]} if shape == "list" else VALUES
    calls = []
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            document, invoice, {1: IMAGE}, TENANT, transport=transport_for(calls, payload=payload))
    record = result["records"][0] if shape == "list" else result
    prefix = "records[0]." if shape == "list" else ""
    assert len(calls) == 1 and record["seller"]["$confidence"] == 3
    assert review and usage["review_reasons"] == ["vision_value_unverified"]
    assert set(usage["vision_unverified_fields"]) == {f"{prefix}items[0].{name}" for name in VALUES["items"][0]}
    row = record["items"][0]
    assert {name: row[name] for name in VALUES["items"][0]} == VALUES["items"][0]
    assert all(cell["$hits"] == [] for cell in row["$cells"].values())


def test_markdown_and_unformatted_raw_values_corroborate_without_coordinates(channels):
    invoice = skill(review_policy={"mode": "never"})
    invoice.fields = [
        FieldSpec(name="amount", type="number", output_format={"decimal_places": 2}),
        FieldSpec(name="comment", mode="inferred"),
        FieldSpec(name="items", type="table", columns=[
            {"name": "price", "type": "number", "output_format": {"decimal_places": 2}},
            {"name": "guess", "mode": "inferred"}, {"name": "optional"}]),
    ]
    payload = {"amount": "1542", "comment": {"value": "estimate", "reasoning": "synthetic"},
               "items": [{"price": "18", "guess": "estimated total", "optional": " "}]}
    document = parsed()
    document.full_markdown = "合计 1542 单价 18"
    calls = []
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            document, invoice, {1: IMAGE}, TENANT, transport=transport_for(calls, payload=payload))
    assert len(calls) == 1 and not review and not usage.get("vision_unverified_fields")
    assert result["amount"]["$value"] == "1542.00" and not result["amount"]["$bbox"]
    assert result["items"][0]["price"] == "18.00"
    assert result["items"][0]["$cells"]["price"]["$raw"] == "18"


def test_unconfigured_vision_table_columns_still_check_the_value_text(channels):
    invoice = skill(review_policy={"mode": "never"})
    invoice.fields[1].columns = []
    document = parsed()
    document.full_markdown = ("Paper Co.\n| unit | quantity | tax_rate | tax |\n"
                              "| --- | --- | --- | --- |\n| 吨 |  | 免税 | *** |")
    with as_tenant(TENANT):
        result, usage, review = extract_from_images(
            document, invoice, {1: IMAGE}, TENANT, transport=transport_for([]))
    assert review and usage["vision_unverified_fields"] == ["items[0].quantity"]
    assert result["items"][0]["quantity"] == "2"


def decode_png(uri):
    import base64
    from io import BytesIO

    from PIL import Image

    prefix, encoded = uri.split(",", 1)
    assert prefix == "data:image/png;base64"
    content = base64.b64decode(encoded, validate=True)
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        return image.copy()


@pytest.mark.parametrize("extension", ["jpg", "png", "bmp", "webp"])
def test_image_first_raster_is_real_png_with_original_displayed_pixels(tmp_path, extension):
    from PIL import Image
    from app.tasks.runner import _vision_first_images

    path = tmp_path / ("scan." + extension)
    image = Image.new("RGB", (4, 3), (18, 97, 185))
    image.putpixel((1, 2), (219, 77, 18))
    image.save(path)
    # Compare decoded pixels, especially after lossy JPEG/WebP encoding.
    with Image.open(path) as original:
        expected = original.convert("RGB")
    encoded = _vision_first_images(str(path), parsed())
    actual = decode_png(encoded[1]).convert("RGB")
    assert actual.size == expected.size and actual.tobytes() == expected.tobytes()


def test_multiframe_tiff_maps_local_pages_and_rejects_incomplete_coverage(tmp_path):
    from PIL import Image
    from app.tasks.runner import _vision_first_images

    first = Image.new("RGB", (3, 5), (11, 22, 33))
    second = Image.new("RGBA", (4, 2), (97, 98, 99, 120))
    path = tmp_path / "invoice.tiff"
    first.save(path, save_all=True, append_images=[second])
    encoded = _vision_first_images(str(path), parsed(2))
    assert set(encoded) == {1, 2}
    for number, expected in enumerate((first, second), 1):
        actual = decode_png(encoded[number])
        assert actual.mode == expected.mode and actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
    # A physical child may map local page 1 to the second frame of a shared parent.
    assert _vision_first_images(str(path), parsed(1), source_pages=[2]) == {1: encoded[2]}
    assert _vision_first_images(str(path), parsed(2), source_pages=[2, 1]) == {1: encoded[2], 2: encoded[1]}
    assert _vision_first_images(str(path), parsed(1)) == {}
    assert _vision_first_images(str(path), parsed(3)) == {}
    for requested in ([0], [3], [], [1, 1]):
        assert _vision_first_images(str(path), parsed(1), source_pages=requested) == {}


def test_shared_pdf_maps_pages_without_silently_using_a_partial_subset(tmp_path, monkeypatch):
    from app.parsers import raster
    from app.tasks.runner import _vision_first_images

    path = str(tmp_path / "source.pdf")
    # PDFium's independent renderer is covered by the existing parser tests.
    monkeypatch.setattr(raster, "pdf_pages_to_images", lambda path, limit: {
        page: f"png-{page}" for page in range(1, min(limit, 4) + 1)})
    assert _vision_first_images(path, parsed(2), [2, 4]) == {1: "png-2", 2: "png-4"}
    assert _vision_first_images(path, parsed(2), [2, 5]) == {}
    assert _vision_first_images(path, parsed(1)) == {}
