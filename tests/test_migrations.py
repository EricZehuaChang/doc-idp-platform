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
