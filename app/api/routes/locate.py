"""Dictionary term-location API (KMBP masking demo, mask-guard channel):
POST /api/v1/locate — deterministic full-text location of a term list.

Pure-compute sibling of /process on the same /api/v1 surface, so it inherits
the exact same auth (TenantMiddleware: Bearer ApiKey or session JWT). Business
rules that define this channel:
- the parse is PINNED to the free electronic parsers (pdfplumber for PDF —
  glyph geometry gives value-tight boxes — markitdown for Office); a scanned
  PDF gets an explicit 422 instead of a silent paid-OCR upgrade, so this
  endpoint can never generate an OCR charge;
- no Transaction/FileRecord/ledger rows and no LLM call: the uploaded file
  lives in a temp dir for the duration of the request and is discarded — the
  synchronous response IS the product (customer adds 5 org names on site,
  reruns, every occurrence gets a box).
"""
import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.extraction.confidence import locate_terms
from app.parsers.base import ParserUnavailable
from app.parsers.router import parse_document

router = APIRouter(prefix="/api/v1", tags=["locate"])

_MAX_SIZE = 50 * 1024 * 1024       # same ceiling as /process (Insavlo v1.2.6)
_MAX_TERMS = 200
_MAX_TERM_LEN = 100

# suffix -> pinned free parser. The channel's no-OCR guarantee hangs on this
# map: auto routing (§4.3) may escalate to the paid scan tier on fallback, an
# explicit pin never does. pdfplumber over opendataloader on purpose — only
# pdfplumber fills Block.chars, which is what makes redaction boxes tight.
_PINNED_PARSER = {
    ".pdf": "pdfplumber",
    ".docx": "markitdown", ".xlsx": "markitdown", ".pptx": "markitdown",
    ".doc": "markitdown", ".xls": "markitdown", ".ppt": "markitdown",
}


def _parse_terms(raw: str) -> list[str]:
    """Validate the `terms` form field: JSON array of 1-200 strings, each
    1-100 chars, else 422. Dedupe preserves first-seen order so `misses`
    echoes the operator's list back in a recognizable order."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "terms must be a valid JSON array of strings")
    if not isinstance(data, list) or not data:
        raise HTTPException(422, "terms must be a non-empty JSON array")
    if len(data) > _MAX_TERMS:
        raise HTTPException(422, f"max {_MAX_TERMS} terms per request")
    out: list[str] = []
    seen: set[str] = set()
    for t in data:
        if not isinstance(t, str) or not 1 <= len(t) <= _MAX_TERM_LEN:
            raise HTTPException(
                422, f"each term must be a string of 1-{_MAX_TERM_LEN} characters")
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


@router.post("/locate")
async def locate_endpoint(file: UploadFile = File(...), terms: str = Form(...)):
    term_list = _parse_terms(terms)
    blob = await file.read()
    if len(blob) > _MAX_SIZE:
        raise HTTPException(413, f"file too large: {file.filename}")
    suffix = Path(file.filename or "").suffix.lower()
    pinned = _PINNED_PARSER.get(suffix)
    if pinned is None:
        # images/OFD/unknown are outside the free electronic chain — refuse
        # loudly rather than route to OCR (channel rule: never a paid parse)
        raise HTTPException(
            422, f"不支持的文件类型 {suffix or '(无扩展名)'}:"
                 "需 OCR,名单定位通道仅支持电子档(PDF/Office 文本层)")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(blob)
        try:
            # thread offload: parsing is CPU-bound, same discipline as runner
            udr = await asyncio.to_thread(parse_document, str(path), pinned)
        except ParserUnavailable:
            # scanned PDF (no text layer) or parser missing — explicit 422,
            # never a silent empty result and never an OCR fallback
            raise HTTPException(
                422, "该文件无文本层(扫描件)或解析不可用:"
                     "需 OCR,名单定位通道仅支持电子档")

    # page dims for pixel->percent conversion (0-100, top-left origin)
    dims = {p.page_no: (p.width, p.height) for p in udr.pages}
    hits: list[dict] = []
    misses: list[str] = []
    for term in term_list:
        term_hits = locate_terms(term, udr)
        if not term_hits:
            misses.append(term)
            continue
        for h in term_hits:
            width, height = dims.get(h["page"], (0, 0))
            bbox = h["bbox"]
            if bbox and width and height:
                x0, top, x1, bottom = bbox
                hits.append({
                    "term": term, "page": h["page"],
                    "x": round(x0 / width * 100, 2),
                    "y": round(top / height * 100, 2),
                    "w": round((x1 - x0) / width * 100, 2),
                    "h": round((bottom - top) / height * 100, 2),
                    # 3 = glyph-tight box, 2 = block-bbox fallback (0-3 scale)
                    "confidence": 3 if h["tight"] else 2,
                })
            else:
                # geometry-free parse (markitdown Office): the term IS in the
                # document but no box can be drawn — weakly located (1),
                # coordinates null. Never dropped: a silent drop would read
                # as a miss and undermine the recall-first channel contract.
                hits.append({"term": term, "page": h["page"],
                             "x": None, "y": None, "w": None, "h": None,
                             "confidence": 1})
    # stable reading order for the masking overlay; null-coord hits sort first
    hits.sort(key=lambda e: (e["page"],
                             e["y"] if e["y"] is not None else -1.0,
                             e["x"] if e["x"] is not None else -1.0))
    return {"page_count": len(udr.pages), "parser": udr.parser,
            "hits": hits, "misses": misses}
