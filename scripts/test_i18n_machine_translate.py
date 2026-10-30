"""Regression checks for missing-only translation and placeholder protection."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("i18n_machine_translate.py")
spec = importlib.util.spec_from_file_location("i18n_translate", SCRIPT)
translate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(translate)


class TranslationTests(unittest.TestCase):
    def test_missing_only_batches_and_literal_escaping(self):
        with tempfile.TemporaryDirectory(dir=SCRIPT.parent / "i18n") as directory:
            root = Path(directory)
            source = {"existing": "保留", **{f"key_{i}": "内容 {name}" for i in range(41)}}
            (root / "zh.json").write_text(json.dumps(source), encoding="utf-8")
            (root / "en.json").write_text(json.dumps({"existing": "Hand reviewed"}), encoding="utf-8")
            batches = []

            def fake_translate(key, language, items):
                batches.append(items)
                return {name: "Content {name} @ |" for name in items}

            with patch.object(translate, "LOCALES", root), patch.object(translate, "translate_batch", fake_translate), patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-only"}), patch("sys.argv", [str(SCRIPT)]), contextlib.redirect_stdout(io.StringIO()):
                translate.main()
                first = (root / "en.json").read_bytes()
                translate.main()
                self.assertEqual((root / "en.json").read_bytes(), first)
            result = json.loads(first)
            self.assertEqual(result["existing"], "Hand reviewed")
            self.assertEqual(sorted(map(len, batches)), [1, 40])
            self.assertEqual(set(result), set(source))
            self.assertEqual(result["key_0"], "Content {name} {'@'} {'|'}")

    def test_response_contract_and_placeholder_mismatch_fail_closed(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": '{"k":"Missing parameter"}'}}]}

        with patch.object(translate.httpx, "post", return_value=Response()) as request, patch.object(translate.time, "sleep"):
            with self.assertRaises(RuntimeError):
                translate.translate_batch("test-only", "English", {"k": "内容 {name}"})
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
