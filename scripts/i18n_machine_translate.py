"""Fill missing English UI messages; existing translations are never overwritten.

Adapted from EricZehuaChang/KBase scripts/i18n_machine_translate.py.
DEEPSEEK_API_KEY must be supplied by the caller's environment.
Use --inventory only to create the one-time codemod English slug cache.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

LOCALES = Path(__file__).resolve().parents[1] / "frontend/src/i18n/locales"
LANG_NAMES = {"en": "English"}
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
BATCH = 40


def flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, name))
        else:
            out[name] = value
    return out


def unflatten(flat):
    out = {}
    for key, value in flat.items():
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def placeholders(text):
    return set(re.findall(r"\{([A-Za-z_][A-Za-z_0-9]*)\}", text))


def escape_message(text):
    # Literal interpolation escapes are already safe and must not be escaped twice.
    tokens = re.split(r"(\{'[^']*'\})", text)
    return "".join(token if index % 2 else token.replace("@", "{'@'}").replace("|", "{'|'}")
                   for index, token in enumerate(tokens))


def translate_batch(api_key, target_name, items):
    prompt = (
        "You are a professional software UI localizer. Translate the VALUES of "
        f"the JSON object from Chinese into {target_name}. Keep every key unchanged. "
        "Keep all {name} placeholders and literal escapes such as {'@'} and {'|'} exactly. "
        "Keep the product names 'IDP' and 'Noetix' untranslated. Use natural concise UI wording. "
        "Translate all Chinese including examples and quoted content. Do not add placeholders. "
        "Return ONLY a JSON object with the exact same keys and string values."
    )
    for attempt in range(3):
        try:
            response = httpx.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": MODEL, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                    "temperature": 0, "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"}},
                timeout=180.0,
            )
            response.raise_for_status()
            result = json.loads(response.json()["choices"][0]["message"]["content"])
            if set(result) != set(items) or any(not isinstance(v, str) or not v.strip() for v in result.values()):
                raise ValueError("Translation response keys/types do not match input")
            for key, value in result.items():
                if placeholders(value) != placeholders(items[key]):
                    raise ValueError(f"Placeholder mismatch: {key}")
            return result
        except (httpx.HTTPError, ValueError, KeyError) as error:
            if attempt == 2:
                # Never print response bodies: upstream errors may contain sensitive details.
                raise RuntimeError(f"Translation batch failed ({type(error).__name__})") from None
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", action="append", choices=list(LANG_NAMES))
    parser.add_argument("--inventory", type=Path, help="One-time codemod inventory JSON")
    args = parser.parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        sys.exit("DEEPSEEK_API_KEY is required")
    if args.inventory:
        source = json.loads(args.inventory.read_text(encoding="utf-8"))
        # Raw rich-text/concatenation reports are manually rewritten before translation.
        plan = json.loads(args.inventory.with_name("extraction-plan.json").read_text(encoding="utf-8"))
        ids = {edit["id"] for file in plan for edit in file["edits"]}
        source = {key: value for key, value in source.items() if key in ids}
        target = args.inventory.with_name("translations.json")
        jobs = [(target, source, "English", False)]
    else:
        source = flatten(json.loads((LOCALES / "zh.json").read_text(encoding="utf-8")))
        jobs = [(LOCALES / f"{lang}.json", source, LANG_NAMES[lang], True)
                for lang in (args.lang or ["en"])]
    for target, source, language, nested in jobs:
        previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        result = flatten(previous) if nested else previous
        todo = {key: value for key, value in source.items() if key not in result}
        batches = [dict(list(todo.items())[i:i+BATCH]) for i in range(0, len(todo), BATCH)]
        print(f"{target.name}: {len(todo)} missing / {len(source)} total", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            pending = {executor.submit(translate_batch, api_key, language, batch): batch for batch in batches}
            done = 0
            for future in concurrent.futures.as_completed(pending):
                translated = future.result()
                result.update({k: escape_message(v) if nested else v for k, v in translated.items()})
                # Preserve existing values and unknown keys: no destructive alignment/force mode.
                save(target, unflatten(result) if nested else result)
                done += len(translated)
                print(f"  translated {done}/{len(todo)}", flush=True)
        for key, value in source.items():
            if key in result and placeholders(value) != placeholders(result[key]):
                raise ValueError(f"Placeholder mismatch: {key}")


if __name__ == "__main__":
    main()
