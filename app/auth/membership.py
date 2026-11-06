"""Account mutation locks shared by tenant transfers and existing account flows."""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.tenancy import current_tenant


async def lock_membership(s: AsyncSession, tenant_id: str) -> None:
    """Serialize seat and email decisions, even for historical unregistered IDs."""
    if s.get_bind().dialect.name == "sqlite":
        await s.execute(text("BEGIN IMMEDIATE"))
    else:
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
                        {"tenant": tenant_id})


async def locked_member(s: AsyncSession, user_id: str) -> User | None:
    """Recheck membership under lock when acting on a previously authenticated ID."""
    tenant = current_tenant()
    await lock_membership(s, tenant)
    return await s.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant)
                          .with_for_update())
