"""Separate OCR channel for the BOC private deployment (2026-11-20).

The bank recognises scans with PaddleOCR-VL (`paddleocrvl16` on its gateway),
a model trained on fixed task prompts ("OCR:"). These tests pin the contract:
idp.env OCR_MODEL renders a `vlm-ocr` parser that becomes the default scan
route; that parser sends the task prompt instead of the long transcription
brief; and the fast-mode / image-first routes never treat it as a vision
channel for extraction prompts, so the general multimodal model keeps that job.
"""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from app.config import ParserCfg

REPO = Path(__file__).resolve().parents[1]


def _render(tmp_path, monkeypatch, env: dict) -> tuple[dict, dict]:
    """Run deploy/docker/render_configs.py against the repo defaults."""
    for k in ("LLM", "VLM", "OCR"):
        for f in ("BASE_URL", "MODEL", "API_KEY", "EXTRA_BODY", "PROMPT"):
            monkeypatch.delenv(f"{k}_{f}", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        "render_configs", REPO / "deploy" / "docker" / "render_configs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DEFAULTS", REPO / "configs")
    monkeypatch.setattr(mod, "OUT", tmp_path)
    mod.main()
    load = lambda n: yaml.safe_load((tmp_path / n).read_text(encoding="utf-8"))  # noqa: E731
    return load("providers.yaml"), load("parsers.yaml")


GATEWAY = {"LLM_BASE_URL": "http://21.8.146.2:9443/v1", "LLM_MODEL": "llm-chat-core",
           "LLM_API_KEY": "sk-x", "VLM_BASE_URL": "http://21.8.146.2:9443/v1",
           "VLM_MODEL": "llm-vision-core", "VLM_API_KEY": "sk-x"}


def test_ocr_model_becomes_the_default_scan_route_on_the_vlm_gateway(tmp_path, monkeypatch):
    providers, parsers = _render(tmp_path, monkeypatch, {**GATEWAY, "OCR_MODEL": "paddleocrvl16"})
    by_name = {p["name"]: p for p in providers["providers"]}
    assert by_name["vision-ocr"]["model"] == "paddleocrvl16"
    # address and key come from the VLM group when OCR_* are not given
    assert by_name["vision-ocr"]["base_url"] == "http://21.8.146.2:9443/v1"
    assert by_name["vision-ocr"]["api_key_env"] == "VLM_API_KEY"
    # the general multimodal channel is still there for fast mode / image-first
    assert by_name["vision-qwen"]["model"] == "llm-vision-core"

    ocr = next(p for p in parsers["parsers"] if p["name"] == "vlm-ocr")
    assert ocr["provider"] == "vision-ocr" and ocr["prompt"] == "OCR:"
    assert set(parsers["default_parser"].values()) == {"vlm-ocr"}


def test_without_ocr_model_nothing_changes(tmp_path, monkeypatch):
    providers, parsers = _render(tmp_path, monkeypatch, GATEWAY)
    assert "vision-ocr" not in {p["name"] for p in providers["providers"]}
    assert "vlm-ocr" not in {p["name"] for p in parsers["parsers"]}
    assert set(parsers["default_parser"].values()) == {"vlm-qwen"}


def test_empty_key_means_no_auth_instead_of_a_misconfigured_channel(tmp_path, monkeypatch):
    """idp.env says "no key -> leave it empty"; the VLM parser refuses a named
    but empty key variable, so an empty key must render as no key at all."""
    env = {**GATEWAY, "VLM_API_KEY": "", "OCR_MODEL": "paddleocrvl16"}
    providers, _ = _render(tmp_path, monkeypatch, env)
    by_name = {p["name"]: p for p in providers["providers"]}
    assert by_name["vision-qwen"]["api_key_env"] == ""
    assert by_name["vision-ocr"]["api_key_env"] == ""
    assert by_name["deepseek"]["api_key_env"] == "LLM_API_KEY"


@pytest.fixture
def page_file(tmp_path):
    from PIL import Image
    p = tmp_path / "one.png"
    Image.new("RGB", (200, 120), "white").save(p)
    return str(p)


@respx.mock
def test_ocr_parser_sends_the_task_prompt_image_first(page_file, monkeypatch):
    from app.parsers import router
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    cfg = {"parsers": {"vlm-ocr": ParserCfg(name="vlm-ocr", type="cloud_vlm",
                                            provider="vision-qwen", prompt="OCR:")},
           "default_parser": {}}
    monkeypatch.setattr(router, "load_parsers", lambda: cfg)
    route = respx.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
                       ).mock(return_value=httpx.Response(
                           200, json={"choices": [{"message": {"content": "发票号码 12345"}}]}))

    udr = router._make("vlm-ocr").parse(page_file)

    parts = json.loads(route.calls[0].request.read())["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["image_url", "text"]
    assert parts[1]["text"] == "OCR:"                 # not the long transcription brief
    assert udr.full_markdown == "发票号码 12345"


def test_ocr_reader_is_never_a_vision_extraction_channel(monkeypatch):
    from app.extraction import vision_first
    from app.skillengine.schema import FieldSpec, SkillPackage
    from app.tasks import runner
    cfg = {"parsers": {
        "vlm-ocr": ParserCfg(name="vlm-ocr", type="cloud_vlm", provider="vision-ocr", prompt="OCR:"),
        "vlm-qwen": ParserCfg(name="vlm-qwen", type="cloud_vlm", provider="vision-qwen")},
        "default_parser": {}}
    monkeypatch.setattr(runner, "load_parsers", lambda: cfg)
    monkeypatch.setattr(vision_first, "load_parsers", lambda: cfg)

    # fast mode: pinning the OCR reader keeps the normal parse route
    assert runner._pinned_vision_provider("vlm-ocr") is None
    assert runner._pinned_vision_provider("vlm-qwen") == "vision-qwen"

    # image-first: a skill pinned to the reader does not send it extraction prompts
    pkg = SkillPackage(skill_code="inv", name="inv", fields=[FieldSpec(name="no")], parser="vlm-ocr")
    assert vision_first.vision_provider(pkg, "default") != "vision-ocr"
    pkg = SkillPackage(skill_code="inv", name="inv", fields=[FieldSpec(name="no")], parser="vlm-qwen")
    assert vision_first.vision_provider(pkg, "default") == "vision-qwen"
