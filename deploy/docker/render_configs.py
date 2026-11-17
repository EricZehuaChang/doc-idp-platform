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
*_EXTRA_BODY is a JSON object merged into every request (e.g. the switch that
turns the model's thinking output off).
"""
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

DEFAULTS = Path("/app/configs.default")
OUT = Path(os.environ.get("IDP_CONFIG_DIR", "/srv/idp/configs"))
LLM, VLM = "deepseek", "vision-qwen"
SCAN_PARSER = "vlm-qwen"


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


def _channel(name: str, prefix: str) -> dict | None:
    base_url = _env(f"{prefix}_BASE_URL")
    if not base_url:
        return None
    return {"name": name, "model": _env(f"{prefix}_MODEL") or name,
            "base_url": base_url.rstrip("/"), "api_key_env": f"{prefix}_API_KEY",
            "extra_body": _extra(f"{prefix}_EXTRA_BODY")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    channels = [c for c in (_channel(LLM, "LLM"), _channel(VLM, "VLM")) if c]
    providers = {"active": LLM, "fallback": [], "providers": channels}

    parsers = yaml.safe_load((DEFAULTS / "parsers.yaml").read_text(encoding="utf-8"))
    # keep the local parsers; of the multimodal ones only the customer's own
    # survives, so pickers never offer a channel that cannot be reached
    kept = [p for p in parsers["parsers"] if p.get("type") == "local"]
    kept.append({"name": SCAN_PARSER, "type": "cloud_vlm", "provider": VLM,
                 "description": "行内多模态模型 · 扫描件与图片"})
    parsers["parsers"] = kept
    parsers["default_parser"] = {tier: SCAN_PARSER for tier in ("lite", "standard", "air_gapped")}

    header = "# Rendered from idp.env by deploy/docker/render_configs.py at container start — edit idp.env, not this file.\n"
    for name, data in (("providers.yaml", providers), ("parsers.yaml", parsers)):
        (OUT / name).write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                encoding="utf-8")
    shutil.copyfile(DEFAULTS / "detectors.yaml", OUT / "detectors.yaml")

    for label, prefix in (("抽取大模型", "LLM"), ("多模态模型", "VLM")):
        url = _env(f"{prefix}_BASE_URL")
        print(f"[配置] {label}: {url or '未配置'}"
              + (f"  模型={_env(f'{prefix}_MODEL')}" if url else ""), flush=True)


if __name__ == "__main__":
    main()
