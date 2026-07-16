"""Multi-document split (context.md M2 item 7): one uploaded file may bundle
several documents (a batch-scanned stack of invoices). An LLM classifies each
page as document-start vs continuation — cheap by design: per-page text
excerpts only, one call per file, and only for multi-page files.

Fail-open: any LLM/format failure returns a single group (no split) — a wrong
split is worse than no split, and extraction still sees the whole text.
"""
import logging

from app.extraction.provider_client import chat_json_with_fallback
from app.parsers.base import UDR

log = logging.getLogger("idp.splitter")

_EXCERPT_CHARS = 300

_SYSTEM = (
    "你是文档分割助手。用户上传的文件可能把多份独立票据/发票/单据扫描进了同一个文件。"
    "根据每一页的文字开头判断每页是【新文档的第一页】还是【上一页文档的续页】。"
    "判断依据：新的发票号码/抬头/票据类型出现=新文档；表格续行、合计、条款延续=续页。"
    '只输出 JSON：{"pages": [{"page": 页码, "new_doc": true|false}, ...]}，'
    "覆盖每一页，第 1 页恒为 new_doc=true。")


def _excerpt(page) -> str:
    text = page.markdown or " ".join(b.text for b in page.blocks if b.text)
    return " ".join(text.split())[:_EXCERPT_CHARS]


def classify_pages(udr: UDR, transport=None) -> tuple[list[list[int]], dict]:
    """Returns (groups, usage): groups = consecutive page numbers per document,
    e.g. [[1], [2, 3]]. Single group means: do not split."""
    single = [[p.page_no for p in udr.pages]]
    if len(udr.pages) < 2:
        return single, {}
    lines = [f"第{p.page_no}页开头: {_excerpt(p)}" for p in udr.pages]
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": "\n".join(lines)}]
    try:
        raw, usage, used = chat_json_with_fallback(messages, [None], transport=transport)
        marks = {int(e["page"]): bool(e["new_doc"]) for e in raw["pages"]}
    except Exception as e:
        log.warning("page classification failed, not splitting: %s", e)
        return single, {}
    usage = dict(usage)
    usage["provider_used"] = used

    groups: list[list[int]] = []
    for p in udr.pages:
        if marks.get(p.page_no, False) or not groups:   # page 1 always starts
            groups.append([p.page_no])
        else:
            groups[-1].append(p.page_no)
    return groups, usage


def slice_udr(udr: UDR, pages: list[int]) -> UDR:
    """Child UDR for one document: selected pages renumbered from 1, geometry
    and blocks intact (bbox highlights keep working per child)."""
    from app.parsers.base import Page

    wanted = [p for p in udr.pages if p.page_no in set(pages)]
    child_pages = [Page(page_no=i + 1, width=p.width, height=p.height,
                        blocks=p.blocks, markdown=p.markdown)
                   for i, p in enumerate(wanted)]
    return UDR(pages=child_pages,
               full_markdown="\n\n".join(p.markdown for p in wanted if p.markdown)
               or "\n".join(b.text for p in wanted for b in p.blocks if b.text),
               parser=udr.parser, lang=udr.lang)


def split_pdf(src_path: str, pages: list[int], dst_path: str) -> bool:
    """Physical PDF slice so each child opens as its own document in review.
    Returns False when not a PDF / pypdf missing — caller keeps the parent file
    as the child's viewing source (acceptable fallback)."""
    if not src_path.lower().endswith(".pdf"):
        return False
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        log.warning("pypdf not installed; children will share the parent PDF view")
        return False
    try:
        reader = PdfReader(src_path)
        writer = PdfWriter()
        for n in pages:
            writer.add_page(reader.pages[n - 1])
        with open(dst_path, "wb") as fh:
            writer.write(fh)
        return True
    except Exception as e:
        log.warning("pdf slice failed (%s); children share the parent view", e)
        return False
