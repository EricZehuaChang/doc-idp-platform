"""Multi-document split tests (context.md M2 item 7). LLM is always mocked
(no tokens); pypdf slices a real 3-page PDF built in-test.
"""
import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, SkillPackage

PKG = SkillPackage(skill_code="split_test", name="t",
                   fields=[FieldSpec(name="invoice_no", instruction="号码")])


def _udr3() -> UDR:
    return UDR(pages=[
        Page(page_no=1, width=595, height=842, markdown="发票号码 INV-A 抬头甲公司",
             blocks=[Block(text="INV-A", bbox=[1, 2, 3, 4])]),
        Page(page_no=2, width=595, height=842, markdown="发票号码 INV-B 抬头乙公司",
             blocks=[Block(text="INV-B", bbox=[5, 6, 7, 8])]),
        Page(page_no=3, width=595, height=842, markdown="合计（续上页）",
             blocks=[Block(text="合计", bbox=[9, 10, 11, 12])]),
    ], full_markdown="x", parser="test")


def test_classify_pages_groups_consecutive(monkeypatch):
    import app.extraction.splitter as sp
    monkeypatch.setattr(sp, "chat_json_with_fallback",
                        lambda messages, chain, transport=None: (
                            {"pages": [{"page": 1, "new_doc": True},
                                       {"page": 2, "new_doc": True},
                                       {"page": 3, "new_doc": False}]},
                            {"prompt_tokens": 50, "completion_tokens": 20}, "fake"))
    groups, usage = sp.classify_pages(_udr3())
    assert groups == [[1], [2, 3]]
    assert usage["provider_used"] == "fake"


def test_classify_failure_fails_open(monkeypatch):
    import app.extraction.splitter as sp

    def boom(messages, chain, transport=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(sp, "chat_json_with_fallback", boom)
    groups, usage = sp.classify_pages(_udr3())
    assert groups == [[1, 2, 3]]      # one group = no split
    assert usage == {}


def test_single_page_never_calls_llm(monkeypatch):
    import app.extraction.splitter as sp

    def boom(messages, chain, transport=None):
        raise AssertionError("must not be called")

    monkeypatch.setattr(sp, "chat_json_with_fallback", boom)
    udr = UDR(pages=[Page(page_no=1)], full_markdown="x", parser="t")
    assert sp.classify_pages(udr)[0] == [[1]]


def test_slice_udr_renumbers_and_keeps_geometry():
    from app.extraction.splitter import slice_udr
    child = slice_udr(_udr3(), [2, 3])
    assert [p.page_no for p in child.pages] == [1, 2]
    assert child.pages[0].width == 595
    assert child.pages[0].blocks[0].text == "INV-B"
    assert "INV-B" in child.full_markdown and "INV-A" not in child.full_markdown


def test_end_to_end_split_flow(tmp_path, monkeypatch):
    """Full runner path on a real 3-page PDF: parse -> classify (mock: doc A =
    p1, doc B = p2-3) -> two children extracted, physical PDF sliced, parent
    split, transaction rolls up, split webhook fired."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from pypdf import PdfWriter
    pdf_path = tmp_path / "bundle.pdf"
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=595, height=842)
    with open(pdf_path, "wb") as fh:
        w.write(fh)

    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: _udr3())
    monkeypatch.setattr(runner_mod, "extract",
                        lambda udr, pkg: ({"invoice_no": {"$value": udr.pages[0].blocks[0].text,
                                                          "$confidence": 3, "$bbox": [1, 2, 3, 4],
                                                          "$pages": 1}},
                                          {"prompt_tokens": 10, "completion_tokens": 5}, False))
    import app.extraction.splitter as sp
    monkeypatch.setattr(sp, "chat_json_with_fallback",
                        lambda messages, chain, transport=None: (
                            {"pages": [{"page": 1, "new_doc": True},
                                       {"page": 2, "new_doc": True},
                                       {"page": 3, "new_doc": False}]},
                            {"prompt_tokens": 50, "completion_tokens": 20}, "fake"))
    fired = []
    async def fake_fire(tenant, event, payload, transport=None):
        fired.append((event, payload))
    monkeypatch.setattr(runner_mod.webhooks, "fire", fake_fire)

    async def main():
        from sqlalchemy import select

        from app.db import init_db, session_factory
        from app.models import FileRecord, SkillVersion, Transaction
        await init_db()
        sf = session_factory()
        async with sf() as s:
            s.add(SkillVersion(tenant_id="default", skill_code="split_test", version=1,
                               status="published", package=PKG.model_dump()))
            txn = Transaction(tenant_id="default", skill_code="split_test", skill_version=1)
            s.add(txn)
            await s.flush()
            parent = FileRecord(tenant_id="default", transaction_id=txn.id,
                                file_name="bundle.pdf", storage_path=str(pdf_path))
            s.add(parent)
            await s.commit()
            txn_id, parent_id = txn.id, parent.id

        await runner_mod.process_transaction(txn_id)

        async with sf() as s:
            parent = await s.get(FileRecord, parent_id)
            children = (await s.execute(select(FileRecord).where(
                FileRecord.parent_file_id == parent_id))).scalars().all()
            txn = await s.get(Transaction, txn_id)
            return parent, sorted(children, key=lambda c: c.file_name), txn

    parent, children, txn = asyncio.run(main())

    assert parent.status == "split" and parent.result is None
    assert [c.file_name for c in children] == ["bundle#doc1.pdf", "bundle#doc2.pdf"]
    assert [c.page_count for c in children] == [1, 2]
    # each child extracted its own document's value
    assert children[0].result["invoice_no"]["$value"] == "INV-A"
    assert children[1].result["invoice_no"]["$value"] == "INV-B"
    assert all(c.status == "completed" for c in children)
    # physical PDF slices exist and differ from the parent path
    from pypdf import PdfReader
    from app.storage import get_storage
    st = get_storage()
    assert len(PdfReader(st.local_path(children[0].storage_path)).pages) == 1
    assert len(PdfReader(st.local_path(children[1].storage_path)).pages) == 2
    # split parents settle the transaction
    assert txn.status == "completed"
    assert any(e == "file.split" and p["documents"] == 2 for e, p in fired)


@pytest.mark.asyncio
async def test_split_is_one_task_across_product_surfaces(tmp_path, monkeypatch):
    """A root upload is one task in status/list/queue/review; child jobs stay
    nested and tenant scoped. The review detail retains all six original pages
    while child results declare their offsets into that file."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/grouped.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    from app.db import init_db, session_factory
    from app.models import FileRecord, Transaction
    await init_db()

    def write_udr(name: str, pages: int):
        path = tmp_path / name
        path.write_text(json.dumps({
            "pages": [{"page_no": i, "width": 595, "height": 842}
                      for i in range(1, pages + 1)]
        }), encoding="utf-8")
        return str(path)

    root_blob = tmp_path / "bundle.pdf"
    root_blob.write_bytes(b"%PDF grouped-test")
    from datetime import datetime, timedelta, timezone
    sf = session_factory()
    async with sf() as s:
        txn = Transaction(tenant_id="default", skill_code="invoice", skill_version=1,
                          status="completed")
        s.add(txn)
        await s.flush()
        root = FileRecord(
            tenant_id="default", transaction_id=txn.id, file_name="bundle.pdf",
            storage_path=str(root_blob), udr_path=write_udr("root.json", 6),
            status="split", page_count=6)
        s.add(root)
        await s.flush()
        # the root's own processing time: a split parent never extracts, so its
        # finished-at is the last child's (需求1 aggregation)
        finish = datetime.now(timezone.utc) + timedelta(seconds=5)
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        for i, (pages, status) in enumerate(((2, "pending_verification"),
                                             (1, "passed"),
                                             (3, "pending_verification")), 1):
            child = FileRecord(
                tenant_id="default", transaction_id=txn.id, parent_file_id=root.id,
                file_name=f"bundle#doc{i}.pdf", storage_path=str(root_blob),
                udr_path=write_udr(f"child{i}.json", pages), status=status,
                processed_at=finish,
                # explicit increasing stamps: rows flushed together share one
                # created_at default, and ordering must never fall to uuid tie-break
                created_at=base + timedelta(seconds=i),
                page_count=pages, result={
                    "invoice_no": {"$value": f"INV-{i}", "$confidence": 3,
                                   "$pages": 1, "$bbox": [1, 2, 3, 4]}})
            s.add(child)

        foreign_txn = Transaction(tenant_id="other", skill_code="invoice", skill_version=1)
        s.add(foreign_txn)
        await s.flush()
        foreign = FileRecord(
            tenant_id="other", transaction_id=foreign_txn.id, file_name="private.pdf",
            storage_path=str(root_blob), status="pending_verification", page_count=1)
        s.add(foreign)
        await s.commit()
        txn_id, root_id, foreign_id = txn.id, root.id, foreign.id

    from app.main import create_app
    async with AsyncClient(transport=ASGITransport(app=create_app()),
                           base_url="http://test") as client:
        status = (await client.get(f"/api/v1/status/{txn_id}")).json()
        assert len(status["files"]) == 1
        assert status["files"][0]["file_id"] == root_id
        assert status["files"][0]["child_count"] == 3
        assert len(status["files"][0]["children"]) == 3

        files = (await client.get("/api/v1/files")).json()
        assert files["total"] == 1 and len(files["data"]) == 1
        assert files["data"][0]["file_id"] == root_id
        assert files["data"][0]["status"] == "pending_verification"
        assert files["data"][0]["pending_children"] == 2
        # per-document speed on a split parent = last child finished − created
        assert files["data"][0]["processing_seconds"] == 5.0
        assert files["data"][0]["processed_at"] is not None
        assert (await client.get("/api/v1/files", params={"status": "passed"})).json()[
            "total"] == 0

        queue = (await client.get("/api/v1/review/queue")).json()
        assert len(queue) == 1 and queue[0]["file_id"] == root_id
        assert queue[0]["child_count"] == 3 and queue[0]["pending_children"] == 2

        detail = (await client.get(f"/api/v1/review/{root_id}")).json()
        assert detail["file_id"] == root_id and len(detail["pages"]) == 6
        assert [c["page_offset"] for c in detail["children"]] == [0, 2, 3]
        first_child_id = detail["children"][0]["file_id"]
        by_child_url = (await client.get(f"/api/v1/review/{first_child_id}")).json()
        assert by_child_url["file_id"] == root_id
        assert len(by_child_url["children"]) == 3

        assert (await client.get(f"/api/v1/review/{foreign_id}")).status_code == 404
        foreign_files = (await client.get(
            "/api/v1/files", headers={"X-Tenant-Id": "other"})).json()
        assert foreign_files["total"] == 1
        assert foreign_files["data"][0]["file_id"] == foreign_id
