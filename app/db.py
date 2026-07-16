"""Async database bootstrap. lite tier = SQLite file, standard+ = PostgreSQL
(design v0.2 §9.1). Business rule: the DB state machine is the single source of
truth for tasks (HA report v2.0 §2.4) — queues/caches may be lost and rebuilt.

Tenant isolation, DB layer (§11.4 pooled+RLS): on PostgreSQL every table that
carries tenant_id gets FORCE ROW LEVEL SECURITY with a policy pinned to the
app.tenant_id GUC; a Session event sets that GUC (transaction-local) from the
request's tenant contextvar on every transaction. Unset GUC = zero rows
(fail closed). NOTE: superusers bypass RLS by definition — production must
connect as a plain role (see ensure_app_role / docker-compose.dev.yml notes);
the dev superuser keeps working because the application layer still filters.
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session as OrmSession

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, echo=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


@event.listens_for(OrmSession, "after_begin")
def _set_tenant_guc(session, transaction, connection):
    """Pin the RLS tenant for this transaction. Runs for every ORM session in
    the process (sync event bridged under the async session); SQLite no-ops."""
    if connection.dialect.name != "postgresql":
        return
    from app.tenancy import current_tenant  # late import: avoid config cycle
    # set_config(..., is_local=true) == SET LOCAL: scope dies with the tx, so
    # pooled connections can never leak a tenant into the next request
    connection.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                       {"t": current_tenant()})


def _tenant_tables():
    """Every business table carrying tenant_id (§11.1). tenants itself is the
    registry row, not tenant-scoped data."""
    return [t for t in Base.metadata.tables.values()
            if "tenant_id" in t.columns and t.name != "tenants"]


async def apply_rls(conn) -> list[str]:
    """Idempotent RLS DDL on an async connection. FORCE so even the table
    owner is subject to the policy (only superusers bypass)."""
    names = []
    for t in _tenant_tables():
        q = f'"{t.name}"'
        await conn.exec_driver_sql(f"ALTER TABLE {q} ENABLE ROW LEVEL SECURITY")
        await conn.exec_driver_sql(f"ALTER TABLE {q} FORCE ROW LEVEL SECURITY")
        await conn.exec_driver_sql(f"DROP POLICY IF EXISTS tenant_isolation ON {q}")
        await conn.exec_driver_sql(
            f"CREATE POLICY tenant_isolation ON {q}"
            " USING (tenant_id = current_setting('app.tenant_id', true))"
            " WITH CHECK (tenant_id = current_setting('app.tenant_id', true))")
        names.append(t.name)
    return names


async def ensure_app_role(conn, role: str, password: str) -> None:
    """Create/refresh the non-superuser login role the app should use in
    production (superusers bypass RLS). Grants full DML; RLS does the fencing."""
    if not role.isidentifier():           # DDL can't bind params; refuse weird names
        raise ValueError(f"invalid role name: {role!r}")
    password = password.replace("'", "''")
    await conn.exec_driver_sql(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '%s')"
        " THEN CREATE ROLE %s LOGIN; END IF; END $$;" % (role, role))
    await conn.exec_driver_sql(f"ALTER ROLE {role} LOGIN PASSWORD '{password}'")
    await conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
    await conn.exec_driver_sql(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")


async def init_db() -> None:
    """M1: create_all on startup. TODO(M1.5): switch to Alembic migrations
    (two-version backward compatibility rule, feasibility v2.0 §3.2)."""
    from app import models  # noqa: F401  register tables

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if engine.dialect.name == "postgresql":
        # separate tx: RLS DDL needs table ownership. When the app runs as the
        # fenced idp_app role (production posture), DDL belongs to provisioning
        # — degrade to a warning instead of refusing to boot.
        try:
            async with engine.begin() as conn:
                await apply_rls(conn)
        except Exception as e:  # InsufficientPrivilege in practice
            import logging
            logging.getLogger("app.db").warning(
                "apply_rls skipped (run provisioning as table owner): %s", e)
