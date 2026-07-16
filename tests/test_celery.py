"""Celery pooled-queue tests (design v0.2 §9).

- routing: GPU-backed parsers go to parse_gpu, the rest to parse_cpu
- eager end-to-end: the plan -> parse -> extract -> finalize canvas drives a
  transaction to completed (business logic without a broker)
- real broker (SKIP without Redis): an actual `-P threads` worker — the
  Windows dev worker mode — answers a ping through Redis
"""
import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.parsers.base import UDR, Block, Page
from app.skillengine.schema import FieldSpec, SkillPackage
from app.tasks.celery_app import QUEUES, celery_app, parse_queue_for

REDIS_ADDR = ("127.0.0.1", 6379)

UDR_SAMPLE = UDR(pages=[Page(page_no=1, width=100, height=100, blocks=[
    Block(text="INV-1", bbox=[1, 2, 3, 4])])], full_markdown="INV-1", parser="test")

PKG = SkillPackage(skill_code="celery_test", name="t",
                   fields=[FieldSpec(name="invoice_no", instruction="号码")])


def _reachable(addr) -> bool:
    try:
        with socket.create_connection(addr, timeout=1):
            return True
    except OSError:
        return False


def test_parse_queue_routing():
    assert parse_queue_for("monkeyocr") == "parse_gpu"
    assert parse_queue_for("glm-ocr-local") == "parse_gpu"
    assert parse_queue_for("glm-ocr-cloud") == "parse_cpu"
    assert parse_queue_for(None) == "parse_cpu"
    assert set(QUEUES) == {"orchestrate", "parse_cpu", "parse_gpu", "extract"}
    routed = {r["queue"] for r in celery_app.conf.task_routes.values()}
    assert routed <= set(QUEUES)


def test_eager_canvas_end_to_end(tmp_path, monkeypatch):
    """Whole canvas in eager mode: proves orchestration + failure isolation
    logic independent of the broker. Sync test on purpose — eager tasks call
    asyncio.run, which must not happen inside a running loop."""
    monkeypatch.setenv("IDP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    import app.db as db
    config.get_settings.cache_clear()
    db._engine = None
    db._session_factory = None

    import app.tasks.runner as runner_mod
    monkeypatch.setattr(runner_mod, "parse_document", lambda path, pinned=None: UDR_SAMPLE)
    monkeypatch.setattr(runner_mod, "extract",
                        lambda udr, pkg: ({"invoice_no": {"$value": "INV-1", "$confidence": 3,
                                                          "$bbox": [1, 2, 3, 4], "$pages": [1]}},
                                          {"prompt_tokens": 10, "completion_tokens": 5}, False))

    async def _seed() -> tuple[str, str]:
        from app.db import init_db, session_factory
        from app.models import FileRecord, SkillVersion, Transaction
        await init_db()
        async with session_factory()() as s:
            s.add(SkillVersion(tenant_id="default", skill_code="celery_test", version=1,
                               status="published", package=PKG.model_dump()))
            txn = Transaction(tenant_id="default", skill_code="celery_test", skill_version=1)
            s.add(txn)
            await s.flush()
            f = FileRecord(tenant_id="default", transaction_id=txn.id,
                           file_name="a.pdf", storage_path=str(tmp_path / "a.pdf"))
            s.add(f)
            await s.commit()
            return txn.id, f.id

    txn_id, file_id = asyncio.run(_seed())

    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    from app.tasks.celery_tasks import run_transaction
    run_transaction.delay(txn_id, "default")

    async def _check():
        from app.db import session_factory
        from app.models import FileRecord, Transaction
        async with session_factory()() as s:
            f = await s.get(FileRecord, file_id)
            txn = await s.get(Transaction, txn_id)
            return f.status, f.result, txn.status

    f_status, f_result, t_status = asyncio.run(_check())
    assert f_status == "completed"
    assert f_result["invoice_no"]["$value"] == "INV-1"
    assert t_status == "completed"


@pytest.mark.skipif(not _reachable(REDIS_ADDR), reason="redis container not running")
def test_real_worker_threads_pool_ping():
    """Real Redis broker + real worker in the documented Windows dev mode
    (-P threads). Proves serialization, routing and result backend end to end."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "app.tasks.celery_app", "worker",
         "-P", "threads", "-c", "4", "-Q", ",".join(QUEUES), "--without-heartbeat",
         "--loglevel", "warning"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        from app.tasks.celery_tasks import ping
        deadline = time.time() + 40
        result = ping.delay()
        while True:
            try:
                assert result.get(timeout=max(1, deadline - time.time())) == "pong"
                break
            except Exception:
                if time.time() > deadline:
                    raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
