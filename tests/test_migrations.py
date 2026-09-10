"""WP1 (docs/ARCHITECTURE.md §4): schema changes go through Alembic.

① a fresh `upgrade head` produces exactly the ORM metadata (no drift);
② the same holds on PostgreSQL (skipped without the dev container);
③ a pre-alembic database (create_all world) is adopted via `stamp head`,
   after which `upgrade head` is a no-op and the schema still matches the
   metadata — this is the production adoption path, tested before it is
   ever run against a real deployment.
"""
import asyncio
import socket
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app import models  # noqa: F401  register tables on the metadata
from app import integrations  # noqa: F401
import app.integrations.webhooks  # noqa: F401  webhooks table lives here, not in models.py
from app.db import Base, alembic_config

PG_ADDR = ("127.0.0.1", 5432)
PG_ADMIN_DSN = "postgresql://idp:idp_dev_pw@127.0.0.1:5432/postgres"
PG_URL = "postgresql+asyncpg://idp:idp_dev_pw@127.0.0.1:5432/idp_mig_test"


def _pg_up() -> bool:
    try:
        with socket.create_connection(PG_ADDR, timeout=1):
            return True
    except OSError:
        return False


async def _assert_schema_matches(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:

            def _check(sync_conn):
                diff = compare_metadata(
                    MigrationContext.configure(sync_conn), Base.metadata)
                assert diff == [], f"schema drift after upgrade head: {diff}"

            await conn.run_sync(_check)
    finally:
        await engine.dispose()


async def test_fresh_upgrade_head_matches_models(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/mig.db"
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    await _assert_schema_matches(url)


@pytest.mark.skipif(not _pg_up(), reason="postgres container not running")
async def test_fresh_upgrade_head_matches_models_pg():
    asyncpg = pytest.importorskip("asyncpg")
    admin = await asyncpg.connect(PG_ADMIN_DSN)
    try:
        await admin.execute("DROP DATABASE IF EXISTS idp_mig_test")
        await admin.execute("CREATE DATABASE idp_mig_test")
    finally:
        await admin.close()
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(PG_URL), "head")
        await _assert_schema_matches(PG_URL)
    finally:
        admin = await asyncpg.connect(PG_ADMIN_DSN)
        try:
            await admin.execute("DROP DATABASE IF EXISTS idp_mig_test")
        finally:
            await admin.close()


async def test_adopt_pre_alembic_db_via_stamp(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/legacy.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   # the old world
    await engine.dispose()

    await asyncio.to_thread(command.stamp, alembic_config(url), "head")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    await _assert_schema_matches(url)

    engine = create_async_engine(url)
    async with engine.connect() as conn:
        version = (await conn.exec_driver_sql(
            "SELECT version_num FROM alembic_version")).scalar()
    await engine.dispose()
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    assert version == head, "stamp head must pin the current head"


BASELINE = "c8c989251f69"


async def _columns(url: str, table: str) -> list[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).all()
        return [r[1] for r in rows]
    finally:
        await engine.dispose()


async def test_adopt_baseline_frozen_db_via_stamp(tmp_path):
    """The real production shape (2026-09-10 deployment).

    A create_all-era database is frozen at the **baseline** revision, not at
    today's models — every revision added after the baseline never ran there.
    Stamping `head` marks those later revisions as applied and silently skips
    them: on production that left `users.session_epoch` missing, and since
    `app/tenancy.py` reads it on every authenticated request, login answered
    HTTP 500. Adoption must pin the baseline, then let the later revisions run.

    `test_adopt_pre_alembic_db_via_stamp` above covers a different shape — a
    database whose schema already equals today's models — which is why it did
    not catch this.
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/frozen.db"
    await asyncio.to_thread(command.upgrade, alembic_config(url), BASELINE)

    # the premise: this column only arrives in a post-baseline revision
    assert "session_epoch" not in await _columns(url, "users"), \
        "premise broken: baseline already has session_epoch"

    await asyncio.to_thread(command.stamp, alembic_config(url), BASELINE)
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

    assert "session_epoch" in await _columns(url, "users"), \
        "adoption skipped a post-baseline revision"
    await _assert_schema_matches(url)          # and lands on today's models

    engine = create_async_engine(url)
    async with engine.connect() as conn:
        version = (await conn.exec_driver_sql(
            "SELECT version_num FROM alembic_version")).scalar()
    await engine.dispose()
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    assert version == head, "adoption must end at head"


async def test_storage_key_backfill_migration(tmp_path, monkeypatch):
    """0002: absolute data_dir paths (pre-WP2 rows) become relative keys."""
    import app.config as config
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path / "data"))
    config.get_settings.cache_clear()
    url = f"sqlite+aiosqlite:///{tmp_path}/backfill.db"
    engine = create_async_engine(url)
    try:
        await asyncio.to_thread(
            command.upgrade, alembic_config(url), "c8c989251f69")
        root = str(config.get_settings().data_dir)
        legacy = str(Path(root) / "files" / "t1" / "x1" / "h.pdf")
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO files (id, tenant_id, transaction_id, file_name,"
                " storage_path, status, page_count, input_tokens, output_tokens,"
                " cleanup_status, created_at, updated_at) VALUES"
                " ('f1','t1','x1','h.pdf',"
                f" '{legacy}', 'queued', 0, 0, 0, 'keep',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        async with engine.connect() as conn:
            stored = (await conn.exec_driver_sql(
                "SELECT storage_path FROM files WHERE id='f1'")).scalar()
        assert stored == "files/t1/x1/h.pdf", "absolute path must become a key"
    finally:
        await engine.dispose()
        config.get_settings.cache_clear()
