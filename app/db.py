"""Async database bootstrap. lite tier = SQLite file, standard+ = PostgreSQL
(design v0.2 §9.1). Business rule: the DB state machine is the single source of
truth for tasks (HA report v2.0 §2.4) — queues/caches may be lost and rebuilt.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

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


async def init_db() -> None:
    """M1: create_all on startup. TODO(M1.5): switch to Alembic migrations
    (two-version backward compatibility rule, feasibility v2.0 §3.2)."""
    from app import models  # noqa: F401  register tables

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
