"""On-site connectivity check for the customer's model services (run by
`idp.sh check` inside the container, so it tests the same network path and
the same request shape the platform uses).

Runtime: the local ONNX runtime loads on this CPU (see check_runtime).
OCR: the PaddleOCR service saved in the admin console answers a rendered number.
LLM: one JSON-mode chat call — must answer valid JSON, and should not spend
tokens on hidden reasoning (structured extraction needs thinking off; a
reasoning answer is reported so LLM_EXTRA_BODY can be fixed).
VLM: one image call with a rendered number — the answer must contain it.
OCR (only when OCR_MODEL is set): the same image sent the way the vlm-ocr
parser sends it — image first, then the task prompt (PaddleOCR-VL: "OCR:").
Exit code 0 only when every configured channel passes.
"""
import base64
import io
import json
import os
import sys
import time

import httpx

# run as `python /app/deploy/check_models.py`: make the platform package
# importable for the console-configured OCR check
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env(name: str) -> str:
    return os.environ.get(name, "").strip().strip("\r")


def _setting(prefix: str, field: str) -> str:
    """OCR_BASE_URL / OCR_API_KEY fall back to the VLM ones, as in render_configs."""
    value = _env(f"{prefix}_{field}")
    if not value and prefix == "OCR" and field in ("BASE_URL", "API_KEY"):
        value = _env(f"VLM_{field}")
    return value


def _post(prefix: str, messages: list, extra: dict) -> tuple[dict, float]:
    body = {"model": _env(f"{prefix}_MODEL"), "messages": messages, "temperature": 0, **extra}
    raw_extra = _env(f"{prefix}_EXTRA_BODY")
    if raw_extra:
        body.update(json.loads(raw_extra))
    headers = {"Content-Type": "application/json"}
    if _setting(prefix, "API_KEY"):
        headers["Authorization"] = f"Bearer {_setting(prefix, 'API_KEY')}"
    started = time.monotonic()
    resp = httpx.post(f"{_setting(prefix, 'BASE_URL').rstrip('/')}/chat/completions",
                      json=body, headers=headers, timeout=120)
    elapsed = time.monotonic() - started
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json(), elapsed


def _reasoning_used(data: dict) -> bool:
    msg = data["choices"][0]["message"]
    details = (data.get("usage") or {}).get("completion_tokens_details") or {}
    return bool((msg.get("reasoning_content") or "").strip()) or bool(details.get("reasoning_tokens"))


def check_llm() -> bool:
    data, elapsed = _post("LLM", [{"role": "user", "content":
                                   'Reply with JSON only: {"ok": true, "n": 42}'}],
                          {"response_format": {"type": "json_object"}})
    content = data["choices"][0]["message"]["content"] or ""
    try:
        ok = json.loads(content.strip().strip("`").removeprefix("json")).get("n") == 42
    except ValueError:
        ok = False
    print(f"  耗时 {elapsed:.1f}s  返回：{content[:120]!r}")
    if _reasoning_used(data):
        print("  ⚠ 模型仍在输出思考过程：抽取会变慢且可能超时，请按行内服务说明在 "
              "LLM_EXTRA_BODY 里填关闭思考的参数")
    return ok


def _test_image_uri() -> str:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (900, 260), "white")
    try:
        font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 64)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((40, 90), "连通测试 编号 73915", font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def check_ocr_model() -> bool:
    """The OCR model exactly as the vlm-ocr parser calls it: image first, then
    the task prompt (PaddleOCR-VL: "OCR:")."""
    data, elapsed = _post("OCR", [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": _test_image_uri()}},
        {"type": "text", "text": _env("OCR_PROMPT") or "OCR:"}]}], {})
    content = data["choices"][0]["message"]["content"] or ""
    print(f"  耗时 {elapsed:.1f}s  返回：{content[:120]!r}")
    return "73915" in content


def check_vlm() -> bool:
    url = _test_image_uri()
    data, elapsed = _post("VLM", [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": url}},
        {"type": "text", "text": "图片里的编号是多少？只回答数字。"}]}], {})
    content = data["choices"][0]["message"]["content"] or ""
    print(f"  耗时 {elapsed:.1f}s  返回：{content[:120]!r}")
    return "73915" in content


def check_runtime() -> bool:
    """The local ONNX runtime backs seal/signature detection and (through
    magika) Office parsing. Probe it in a child process: if it crashes on
    this CPU, the check reports it instead of dying with it."""
    import subprocess
    probe = ("import onnxruntime as o, magika; "
             "o.InferenceSession(os.environ['IDP_SEAL_MODEL_PATH'], providers=['CPUExecutionProvider']); "
             "print(o.__version__)")
    started = time.monotonic()
    r = subprocess.run([sys.executable, "-c", "import os; " + probe],
                       capture_output=True, text=True, timeout=300)
    if r.returncode == 0:
        print(f"  onnxruntime {r.stdout.strip()}，检测模型加载耗时 {time.monotonic() - started:.1f}s")
        return True
    print(f"  ✗ 进程退出码 {r.returncode}：{(r.stderr or r.stdout).strip()[-300:]}")
    print("  印章/签名检测和 Office 文件解析在本机不可用，请联系研发")
    return False


def check_ocr() -> bool | None:
    """The PaddleOCR service configured in the admin console (Settings ->
    OCR service), called exactly as the platform does. None = not enabled."""
    import asyncio

    from PIL import Image, ImageDraw, ImageFont

    from app.db import session_factory
    from app.extraction import ocr_service
    from app.parsers import paddleocr_http

    async def _load():
        async with session_factory()() as s:
            return await ocr_service.load(s)

    cfg = asyncio.run(_load())
    if not (cfg.get("enabled") and cfg.get("url")):
        print("[跳过] 文字识别服务：后台“设置 → 文字识别服务”未启用")
        return None
    print(f"[检查] 文字识别服务 {cfg['url']}")
    runtime = ocr_service.runtime(cfg)
    img = Image.new("RGB", (900, 260), "white")
    ImageDraw.Draw(img).text((50, 80), "OCR TEST 73915", fill="black",
                             font=ImageFont.load_default(size=72))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    started = time.monotonic()
    with paddleocr_http.make_client(runtime) as client:
        blocks, _md, shape = paddleocr_http.ocr_png(buf.getvalue(), runtime, client)
    text = " ".join(b.text for b in blocks)
    print(f"  耗时 {time.monotonic() - started:.1f}s  返回：{text[:120]!r}")
    if not any(b.bbox for b in blocks):
        print("  ⚠ 返回结果没有坐标：原文高亮和打码对扫描件不可用")
    return "73915" in text


def main() -> int:
    print("[检查] 本机推理库（印章/签名检测、Office 解析）")
    ok = check_runtime()
    print("  ✓ 通过" if ok else "  ✗ 未通过")
    results = [ok]
    try:
        ocr_ok = check_ocr()
    except Exception as e:                          # report, never crash the check
        print(f"  ✗ 调用失败：{e}")
        ocr_ok = False
    if ocr_ok is not None:
        print("  ✓ 通过" if ocr_ok else "  ✗ 未通过")
        results.append(ocr_ok)
    for label, prefix, fn in (("抽取大模型", "LLM", check_llm), ("多模态模型", "VLM", check_vlm),
                              ("OCR 识别模型", "OCR", check_ocr_model)):
        if prefix == "OCR" and not _env("OCR_MODEL"):
            print("[跳过] OCR 识别模型：idp.env 未填 OCR_MODEL（扫描件走多模态模型）")
            continue
        if not _setting(prefix, "BASE_URL"):
            print(f"[跳过] {label}：idp.env 未填 {prefix}_BASE_URL")
            continue
        print(f"[检查] {label} {_setting(prefix, 'BASE_URL')}  模型={_env(f'{prefix}_MODEL')}")
        try:
            ok = fn()
        except Exception as e:                      # surface every failure in plain words
            print(f"  ✗ 调用失败：{e}")
            ok = False
        print("  ✓ 通过" if ok else "  ✗ 未通过")
        results.append(ok)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
