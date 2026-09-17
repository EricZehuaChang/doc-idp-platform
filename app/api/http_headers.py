"""Shared response-header builders for file downloads.

One implementation on purpose: `/artifacts/{id}/download` and the data-cabinet
CSV export both hand-build `Content-Disposition`, and a missing percent-encode
turns a Chinese filename into a 500 (Starlette encodes headers as latin-1).
"""
from urllib.parse import quote


def content_disposition(name: str) -> str:
    """RFC 5987/6266 attachment header: percent-encoded UTF-8 `filename*` plus
    an ASCII-only `filename` fallback. CR/LF are stripped first so a crafted
    display name can never inject a header line."""
    safe = (name or "").replace("\r", "").replace("\n", "")
    fallback = "".join(ch if 32 <= ord(ch) < 128 and ch not in '"\\' else "_"
                       for ch in safe) or "download"
    return (f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(safe, safe='')}")
