"""VLM parser (2026-08-27): page raster -> multimodal model -> Markdown.

Mocked, because the point under test is the contract with the provider (which
model, which URL, image parts actually attached) and the UDR it builds — not
the model's transcription quality, which was measured live and recorded in
configs/parsers.yaml.
"""
import httpx
import pytest
import respx

from app.parsers.base import ParserUnavailable
from app.parsers.router import _make


@pytest.fixture
def page_file(tmp_path):
    from PIL import Image
    p = tmp_path / "one.png"
    Image.new("RGB", (200, 120), "white").save(p)
    return str(p)


def _reply(text: str):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@respx.mock
def test_sends_the_image_to_the_configured_model_and_builds_udr(page_file, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    route = respx.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    ).mock(return_value=_reply("```markdown\n# 标题\n正文\n```"))

    udr = _make("vlm-qwen").parse(page_file)

    assert route.called
    body = route.calls[0].request.read().decode()
    import json as _json
    sent = _json.loads(body)
    assert sent["model"] == "qwen3-vl-plus"            # from providers.yaml
    parts = sent["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"

    # fences stripped, UDR shaped, provenance names the channel
    assert udr.full_markdown == "# 标题\n正文"
    assert udr.parser == "vlm:vision-qwen"
    assert len(udr.pages) == 1
    # a chat model returns text, not geometry — callers must not expect bbox
    assert udr.pages[0].blocks[0].bbox is None


@respx.mock
def test_each_parser_name_drives_its_own_configured_channel(page_file, monkeypatch):
    """The three names are one class; the model behind each comes from config."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-glm")
    ds = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=_reply("ds"))
    glm = respx.post("https://open.bigmodel.cn/api/paas/v4/chat/completions").mock(
        return_value=_reply("glm"))

    assert _make("vlm-deepseek").parse(page_file).full_markdown == "ds"
    assert _make("vlm-glm").parse(page_file).full_markdown == "glm"
    import json as _json
    assert _json.loads(ds.calls[0].request.read())["model"] == "deepseek-v4-flash-vision-exp"
    assert _json.loads(glm.calls[0].request.read())["model"] == "glm-5.3-flash"


def test_missing_key_is_retryable_not_a_bad_document(page_file, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ParserUnavailable):
        _make("vlm-qwen").parse(page_file)


@respx.mock
def test_long_document_truncation_is_recorded_not_silent(tmp_path, monkeypatch):
    """A partial read must not look like a short document downstream."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    respx.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    ).mock(return_value=_reply("p"))

    import app.parsers.vlm_ocr as vlm

    class _Img:
        def save(self, buf, **kw):
            buf.write(b"x")

    class _P:
        def __init__(self, n):
            self.page_no, self.image, self.width, self.height = n, _Img(), 10.0, 10.0

    monkeypatch.setattr("app.detectors.render.rasterize",
                        lambda path: ([_P(i + 1) for i in range(57)], 57))

    udr = _make("vlm-qwen").parse(str(tmp_path / "any.pdf"))
    assert len(udr.pages) == vlm._MAX_PAGES
    assert udr.parser == f"vlm:vision-qwen(truncated {vlm._MAX_PAGES}/57)"
