"""PG Row-Level Security cross-tenant leak tests (design v0.2 §11.4, the DB
layer of the double defense). Integration tests: SKIP when the dev PG container
(docker-compose.dev.yml) is down, so the suite stays green without Docker.

Proves, connected as a NON-superuser app role:
- tenant A sees only tenant A rows (SELECT fence)
- unset tenant GUC -> zero rows (fail closed)
- INSERT/UPDATE across the fence are rejected/no-ops (WITH CHECK)
- the ORM session event pins the GUC from the tenancy contextvar automatically
"""
import socket

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.db import Base, apply_rls, ensure_app_role
from app.tenancy import _tenant_ctx

PG_ADDR = ("127.0.0.1", 5432)
SUPER_URL = "postgresql+asyncpg://idp:idp_dev_pw@127.0.0.1:5432/idp"
APP_URL = "postgresql+asyncpg://idp_app:idp_app_pw@127.0.0.1:5432/idp"


def _reachable(addr) -> bool:
    try:
        with socket.create_connection(addr, timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(PG_ADDR),
                                reason="postgres container not running")


@pytest.fixture
async def pg(monkeypatch):
    """Fresh schema with RLS applied, seeded with two tenants' users, plus a
    non-superuser engine. Yields (app_engine,)."""
    su = create_async_engine(SUPER_URL)
    async with su.begin() as conn:
        # clean slate: this test owns the dev database schema
        await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA public")
        await conn.run_sync(Base.metadata.create_all)
        await apply_rls(conn)
        await ensure_app_role(conn, "idp_app", "idp_app_pw")
    # seed as superuser (bypasses RLS — that's the point of seeding this way)
    async with su.begin() as conn:
        await conn.execute(text(
            "INSERT INTO users (id, tenant_id, email, role, auth_provider, unlimited, active,"
            " email_verified, must_change_password, created_at) VALUES"
            " ('u1', 't1', 'a@example.com', 'operator', 'local', false, true, true, false, now()),"
            " ('u2', 't2', 'b@example.com', 'operator', 'local', false, true, true, false, now())"))
    await su.dispose()

    app_engine = create_async_engine(APP_URL)
    yield app_engine
    await app_engine.dispose()


async def test_select_fenced_by_tenant(pg):
    async with pg.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 't1', true)"))
        rows = (await conn.execute(text("SELECT id FROM users"))).scalars().all()
        assert rows == ["u1"]                     # t2's row is invisible


async def test_unset_tenant_sees_nothing(pg):
    async with pg.connect() as conn:
        rows = (await conn.execute(text("SELECT id FROM users"))).scalars().all()
        assert rows == []                         # fail closed, not fail open


async def test_cross_tenant_insert_rejected(pg):
    async with pg.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 't1', true)"))
        with pytest.raises(Exception) as exc:
            await conn.execute(text(
                "INSERT INTO users (id, tenant_id, email, role, auth_provider, unlimited,"
                " active, created_at)"
                " VALUES ('evil', 't2', 'evil@example.com', 'admin', 'local', false, true,"
                " now())"))
        assert "row-level security" in str(exc.value).lower()


async def test_cross_tenant_update_is_noop(pg):
    async with pg.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 't1', true)"))
        r = await conn.execute(text("UPDATE users SET role = 'admin' WHERE id = 'u2'"))
        assert r.rowcount == 0                    # can't even see it to touch it
        await conn.rollback()
    # verify t2 unharmed, from t2's own viewpoint
    async with pg.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 't2', true)"))
        role = (await conn.execute(text("SELECT role FROM users WHERE id='u2'"))).scalar_one()
        assert role == "operator"


async def test_orm_session_pins_guc_from_contextvar(pg):
    """The after_begin event must make plain ORM usage tenant-safe with zero
    per-call ceremony — exactly what every route/runner does today."""
    sf = async_sessionmaker(pg, expire_on_commit=False)
    tok = _tenant_ctx.set("t2")
    try:
        async with sf() as s:
            rows = (await s.execute(select(models.User.id))).scalars().all()
            assert rows == ["u2"]
            # UPDATE through ORM against the other tenant: silently fenced out
            r = await s.execute(update(models.User).where(models.User.id == "u1")
                                .values(role="admin"))
            assert r.rowcount == 0
            await s.commit()
    finally:
        _tenant_ctx.reset(tok)
