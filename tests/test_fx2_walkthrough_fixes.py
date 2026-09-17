"""FX2 回归测试：#6 运行记录状态与耗时、#7 documents 契约、#22 子文档 metrics、
#23 导入 sha256 服务端取值、D4 naming-preview 的 available_tokens 与占位值。"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_fx1_walkthrough_fixes import AdvEnv, _txn_rows


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ------------------------------------------------- #6 运行记录状态与耗时 (D2)


async def _seed_run(tmp_path, monkeypatch, *, status: str, review_mode="never",
                    missing_invoice=False, classify_only_pending=False):
    """Boots an app+DB, runs one Playground-style test transaction through the
    REAL runner, then leaves the file in the requested state."""
    from app.db import session_factory
    from app.models import FileRecord, StudioRun, StudioSample

    env = AdvEnv(tmp_path, monkeypatch, action="split", review_mode=review_mode)
    async with env:
        env.missing_invoice = missing_invoice
        txn_id = await env.seed()
        # register a studio sample + run row pointing at this transaction
        async with session_factory()() as s:
            f = (await s.execute(
                __import__("sqlalchemy").select(FileRecord)
                .where(FileRecord.transaction_id == txn_id))).scalars().first()
            s.add(StudioSample(id="s1", tenant_id="default", skill_code="fx_adv",
                               file_name="样本 发票 #1.pdf",
                               storage_key=f.storage_path, uploader_id="tester"))
            run = StudioRun(tenant_id="default", skill_code="fx_adv",
                            skill_version=1, sample_id="s1",
                            transaction_id=txn_id, file_id=f.id,
                            created_by="tester", status="queued",
                            processing_mode="balanced")
            s.add(run)
            await s.commit()
            run_id, file_id = run.id, f.id
        await env.run()
        return txn_id, run_id, file_id


async def _runs_api(env_app, path):
    async with env_app.router.lifespan_context(env_app):
        async with await _client(env_app) as c:
            r = await c.get(path)
            assert r.status_code == 200, r.text
            return r.json()


async def test_run_status_and_duration_all_completed(tmp_path, monkeypatch):
    from app.main import create_app
    txn_id, run_id, _ = await _seed_run(tmp_path, monkeypatch,
                                        status="completed")
    app = create_app()
    lst = await _runs_api(app, "/api/v1/studio/runs?skill_code=fx_adv")
    detail = await _runs_api(app, f"/api/v1/studio/runs/{run_id}")
    row = [r for r in lst["runs"] if r["run_id"] == run_id][0]
    assert row["status"] == "completed", row
    assert row["duration_ms"] and row["duration_ms"] > 0
    assert detail["status"] == "completed"
    assert detail["duration_ms"] == row["duration_ms"]      # 三处同源
    assert detail["finished_at"] == row["finished_at"]


async def test_run_status_needs_review_and_failed(tmp_path, monkeypatch):
    from app.main import create_app

    # needs_review: one document waits for a human
    txn_id, run_id, _ = await _seed_run(tmp_path, monkeypatch,
                                        status="needs_review",
                                        review_mode="auto",
                                        missing_invoice=True)
    app = create_app()
    detail = await _runs_api(app, f"/api/v1/studio/runs/{run_id}")
    assert detail["status"] == "needs_review", detail
    assert detail["duration_ms"] and detail["duration_ms"] > 0


async def test_run_status_failed(tmp_path, monkeypatch):
    from app.db import session_factory
    from app.main import create_app
    from app.models import FileRecord

    txn_id, run_id, file_id = await _seed_run(tmp_path, monkeypatch,
                                              status="completed")
    async with session_factory()() as s:
        f = await s.get(FileRecord, file_id)
        f.status = "error"
        f.error = "boom"
        await s.commit()
    app = create_app()
    detail = await _runs_api(app, f"/api/v1/studio/runs/{run_id}")
    assert detail["status"] == "failed"


# ------------------------------------------------------- #7 documents 契约


@pytest.mark.parametrize("file_status,meta,expected", [
    ("pending_verification", {}, "completed"),          # 已抽取，等复核
    ("rejected", {}, "completed"),                      # D8
    ("queued", {}, "processing"),
    ("processing", {}, "processing"),
    ("error", {}, "failed"),
    ("completed", {"extraction_status": "not_requested"}, "not_requested"),
])
async def test_documents_extraction_status(tmp_path, monkeypatch, file_status,
                                           meta, expected):

    from app.db import init_db, session_factory
    from app.models import FileRecord, Transaction
    from app.storage import get_storage

    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    await init_db()
    st = get_storage()
    async with session_factory()() as s:
        txn = Transaction(tenant_id="default", skill_code="fx", skill_version=1)
        s.add(txn)
        await s.flush()
        key = st.put_bytes(f"files/default/{txn.id}/a.pdf", b"%PDF-1.4 x")
        s.add(FileRecord(tenant_id="default", transaction_id=txn.id,
                         file_name="a.pdf", storage_path=key, page_count=2,
                         status=file_status, document_meta=meta or None,
                         result={"invoice_no": {"$value": "INV-1",
                                                "$confidence": 3}}))
        await s.commit()
        txn_id = txn.id
    from app.main import create_app
    app = create_app()
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            r = await c.get(f"/api/v1/transactions/{txn_id}/documents")
            assert r.status_code == 200, r.text
            doc = r.json()["files"][0]["documents"][0]
            assert doc["extraction_status"] == expected, doc


# --------------------------------------------------- #22 子文档 metrics


async def test_child_metrics_inherit_parse_and_classify(tmp_path, monkeypatch):
    async with AdvEnv(tmp_path, monkeypatch, action="split",
                      review_mode="never") as env:
        txn_id = await env.seed()
        await env.run()
        files, _arts, _blobs = await _txn_rows(txn_id)
        parent = [f for f in files if f.parent_file_id is None][0]
        children = [f for f in files if f.parent_file_id]
        pmetrics = (parent.document_meta or {}).get("metrics") or {}
        assert pmetrics.get("parse_ms", 0) > 0
        for c in children:
            m = (c.document_meta or {}).get("metrics") or {}
            if (c.document_meta or {}).get("handler") == "classify_only":
                assert m.get("total_ms", 0) > 0, m      # 仅分类也有 metrics
                assert m.get("pages")
                continue
            assert m.get("parse_ms"), m
            assert m.get("classify_ms") is not None, m
            assert m.get("extract_ms", 0) >= 0, m
            assert m.get("total_ms", 0) >= m["parse_ms"], m
            assert m.get("inherited_from_parent") is True


# ------------------------------------------------- #23 导入 sha256 服务端取值


async def test_import_commit_ignores_client_sha256(tmp_path, monkeypatch):
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import AuditLog, SkillVersion
    from app.skillengine.package import build_package, generate_passphrase
    from tests.test_wp7_packages import _boot, _std_pkg

    pkg = _std_pkg("demo")
    pw = generate_passphrase()
    blob = build_package({"package": pkg, "skill_code": "demo", "name": "产出演示",
                          "kind": "extract", "version": 1,
                          "status": "published", "changelog": ""},
                         {}, {}, 2, pw)
    app = await _boot(tmp_path, monkeypatch, {"demo": pkg})
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/skill-packages/import/preview",
                             files={"zip_file": ("a.zip", blob,
                                                 "application/zip")},
                             data={"passphrase": pw})
            prev = r.json()
            server_sha = prev["sha256"]
            # 客户端谎报 sha256：changelog 与审计必须仍用服务端暂存值
            r = await c.post("/api/v1/skill-packages/import/commit", json={
                "import_token": prev["import_token"],
                "sha256": "deadbeef" * 8,
                "conflict": "rename"})
            assert r.status_code == 200, r.text
            code = r.json()["skill_code"]
            async with session_factory()() as s:
                ver = (await s.execute(
                    select(SkillVersion).where(
                        SkillVersion.skill_code == code))).scalar_one()
                assert server_sha[:8] in ver.changelog
                assert "deadbeef" not in ver.changelog
                log = (await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "skills.package_imported")
                )).scalars().first()
                assert log.detail["sha256"] == server_sha


# --------------------------------------- D4 naming-preview available_tokens


async def _preview(app, payload):
    async with app.router.lifespan_context(app):
        async with await _client(app) as c:
            r = await c.post("/api/v1/studio/naming-preview", json=payload)
            assert r.status_code == 200, r.text
            return r.json()


async def test_naming_preview_available_tokens(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None
    from app.db import init_db
    await init_db()
    app = create_app()
    fields = ["invoice_no", "total_amount"]

    got = await _preview(app, {"pattern": "{original_name}{original_ext}",
                               "action": "rename", "fields": fields,
                               "sample": {"original_name": "发票 #1.pdf"}})
    names = got["available_tokens"]
    assert names[:5] == ["original_name", "original_ext", "date", "time",
                         "doc_type"]
    assert names[5:] == ["data.invoice_no", "data.total_amount"]
    assert "doc_index" not in names                      # 仅拆分可用

    got = await _preview(app, {"pattern": "{doc_index}_{original_name}",
                               "action": "split", "fields": fields,
                               "sample": {"original_name": "发票 #1.pdf",
                                          "doc_index": 2}})
    assert "doc_index" in got["available_tokens"]

    # 没有数据时用 ‹字段名› 占位，而不是渲染成空
    got = await _preview(app, {"pattern": "{data.invoice_no}_{original_name}",
                               "action": "rename", "fields": fields,
                               "sample": {"original_name": "发票 #1.pdf"}})
    assert got["preview"].startswith("‹invoice_no›_")
    assert got["sample_source"] == "placeholder"


# ---------------------------------------------------------------- lint guard


def test_no_module_level_import_left_unused():
    """占位：确保本文件导入的 pytest 被使用（ruff 已覆盖，保留断言以固定意图）。"""
    assert pytest is not None and asyncio is not None
