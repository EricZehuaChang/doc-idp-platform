"""Render configs/*.yaml for an offline private deployment from environment
variables, so the on-site operator edits one file (idp.env) instead of three
YAMLs. Runs at every container start; output goes to /srv/idp/configs, which
/app/configs links to.

Model channels point straight at the customer's own OpenAI-compatible
services. No public channel and no fallback chain is configured, so a failed
call can never try to leave the customer network.

  LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_EXTRA_BODY   extraction model  -> provider "deepseek"
  VLM_BASE_URL / VLM_MODEL / VLM_API_KEY / VLM_EXTRA_BODY   multimodal model  -> provider "vision-qwen",
                                                            parser "vlm-qwen" = default scan route
  OCR_MODEL / OCR_PROMPT / OCR_EXTRA_BODY                   optional OCR model (2026-11-20) -> provider
  (OCR_BASE_URL / OCR_API_KEY default to the VLM ones)      "vision-ocr", parser "vlm-ocr", which then
                                                            becomes the default scan route
*_EXTRA_BODY is a JSON object merged into every request (e.g. the switch that
turns the model's thinking output off).

Why a separate OCR channel: the bank recognises scans with PaddleOCR-VL, a
model trained on fixed task prompts ("OCR:"). It reads pages; it cannot take
an extraction prompt. The general multimodal model stays the channel for fast
mode / image-first extraction, the OCR model only transcribes scans.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

DEFAULTS = Path("/app/configs.default")
OUT = Path(os.environ.get("IDP_CONFIG_DIR", "/srv/idp/configs"))
LLM, VLM, OCR = "deepseek", "vision-qwen", "vision-ocr"
SCAN_PARSER, OCR_PARSER = "vlm-qwen", "vlm-ocr"
OCR_DEFAULT_PROMPT = "OCR:"            # PaddleOCR-VL's text-recognition task prompt


def _env(name: str) -> str:
    # tolerate CR from an idp.env edited on Windows
    return os.environ.get(name, "").strip().strip("\r")


def _extra(name: str) -> dict:
    raw = _env(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        sys.exit(f"[配置错误] {name} 必须是 JSON 对象，当前值：{raw}")
    if not isinstance(value, dict):
        sys.exit(f"[配置错误] {name} 必须是 JSON 对象（花括号），当前值：{raw}")
    return value


def _key_env(prefix: str) -> str:
    # name the key variable only when it holds a key: an empty api_key_env
    # means "no auth", which is what an empty *_API_KEY in idp.env promises
    # (the VLM parser refuses a named-but-empty key as misconfigured)
    return f"{prefix}_API_KEY" if _env(f"{prefix}_API_KEY") else ""


def _channel(name: str, prefix: str) -> dict | None:
    base_url = _env(f"{prefix}_BASE_URL")
    if not base_url:
        return None
    return {"name": name, "model": _env(f"{prefix}_MODEL") or name,
            "base_url": base_url.rstrip("/"), "api_key_env": _key_env(prefix),
            "extra_body": _extra(f"{prefix}_EXTRA_BODY")}


def _ocr_channel() -> dict | None:
    """OCR model on the VLM gateway unless OCR_BASE_URL / OCR_API_KEY say otherwise."""
    model = _env("OCR_MODEL")
    base_url = _env("OCR_BASE_URL") or _env("VLM_BASE_URL")
    if not (model and base_url):
        return None
    return {"name": OCR, "model": model, "base_url": base_url.rstrip("/"),
            "api_key_env": _key_env("OCR") or _key_env("VLM"),
            "extra_body": _extra("OCR_EXTRA_BODY")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ocr = _ocr_channel()
    channels = [c for c in (_channel(LLM, "LLM"), _channel(VLM, "VLM"), ocr) if c]
    providers = {"active": LLM, "fallback": [], "providers": channels}

    parsers = yaml.safe_load((DEFAULTS / "parsers.yaml").read_text(encoding="utf-8"))
    # keep the local parsers; of the multimodal ones only the customer's own
    # survives, so pickers never offer a channel that cannot be reached
    kept = [p for p in parsers["parsers"] if p.get("type") == "local"]
    kept.append({"name": SCAN_PARSER, "type": "cloud_vlm", "provider": VLM,
                 "description": "行内多模态模型 · 扫描件与图片"})
    scan = SCAN_PARSER
    if ocr:
        kept.append({"name": OCR_PARSER, "type": "cloud_vlm", "provider": OCR,
                     "prompt": _env("OCR_PROMPT") or OCR_DEFAULT_PROMPT,
                     "description": "行内 OCR 模型 · 扫描件与图片（只转写文字）"})
        scan = OCR_PARSER
    parsers["parsers"] = kept
    parsers["default_parser"] = {tier: scan for tier in ("lite", "standard", "air_gapped")}

    header = "# Rendered from idp.env by deploy/docker/render_configs.py at container start — edit idp.env, not this file.\n"
    for name, data in (("providers.yaml", providers), ("parsers.yaml", parsers)):
        (OUT / name).write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                encoding="utf-8")
    shutil.copyfile(DEFAULTS / "detectors.yaml", OUT / "detectors.yaml")

    for label, prefix in (("抽取大模型", "LLM"), ("多模态模型", "VLM")):
        url = _env(f"{prefix}_BASE_URL")
        print(f"[配置] {label}: {url or '未配置'}"
              + (f"  模型={_env(f'{prefix}_MODEL')}" if url else ""), flush=True)
    print("[配置] 扫描件识别: " + (f"OCR 模型 {ocr['model']}（指令 {_env('OCR_PROMPT') or OCR_DEFAULT_PROMPT}）"
                                  if ocr else "多模态模型"), flush=True)


if __name__ == "__main__":
    main()
