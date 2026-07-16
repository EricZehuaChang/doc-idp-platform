"""MonkeyOCR optional GPU parser (design v0.2 §4.2: retained as a formal option;
weights research-licensed — internal/POC/SaaS self-use fine, customer-distributed
deployments need informed consent).

Fork-and-own from KBase m5-1 monkey_http.py which verified the REAL API contract
against MonkeyOCR source (not the docs): POST /parse (multipart field `file`)
returns {success, download_url} — the markdown is inside a zip fetched from
download_url, file `{stem}.md`. No confidence and no bbox are exposed by the
API, so blocks carry markdown text only (verification highlight unavailable on
this parser — GLM-OCR remains the default for that reason).
"""
import io
import os
import zipfile
from pathlib import Path

import httpx

from app.parsers.base import UDR, Block, Page, ParserUnavailable
from app.plugins.registry import registry


@registry.register("parser", "monkeyocr")
class MonkeyOcrHttpParser:
    def __init__(self, base_url: str | None = None, timeout: float = 600.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or os.environ.get(
            "MONKEYOCR_URL", "http://127.0.0.1:7861")).rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def parse(self, path: str) -> UDR:
        p = Path(path)
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(f"{self.base_url}/parse",
                                   files={"file": (p.name, p.read_bytes())})
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success") or not data.get("download_url"):
                    raise ParserUnavailable(
                        f"monkeyocr parse failed: {data.get('message', 'unknown')}")
                zip_resp = client.get(f"{self.base_url}{data['download_url']}")
                zip_resp.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ParserUnavailable(f"monkeyocr unreachable: {e}") from e
        except httpx.HTTPStatusError as e:
            raise ParserUnavailable(f"monkeyocr HTTP {e.response.status_code}") from e

        md = self._extract_md(zip_resp.content, p.stem)
        return UDR(pages=[Page(page_no=1, blocks=[Block(text=md)])],
                   full_markdown=md, parser="monkeyocr")

    @staticmethod
    def _extract_md(blob: bytes, stem: str) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                names = zf.namelist()
                target = next((n for n in names if n.endswith(f"{stem}.md")),
                              next((n for n in names if n.endswith(".md")), None))
                if target is None:
                    raise ParserUnavailable("monkeyocr zip contains no .md file")
                return zf.read(target).decode("utf-8", errors="replace")
        except zipfile.BadZipFile as e:
            raise ParserUnavailable("monkeyocr returned invalid zip") from e
