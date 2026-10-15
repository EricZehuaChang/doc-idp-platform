"""One authorization boundary for task reads, aggregates and review writes.

Only the submitting account (including its owned API keys) and administrators
may access a task. Assignment, review state and reviewer names are workflow
metadata, never access grants. Keys use immutable IDs exclusively.
"""
from fastapi import HTTPException
from sqlalchemy import and_, false, or_, select

from app.models import ApiKey, FileRecord, Transaction
from app.tenancy import current_actor, current_tenant


def task_scope(actor: dict) -> str:
    if actor.get("api_key_id"):
        return "key"
    return "all" if actor.get("role") == "admin" else "own"


def _owner_cond(actor: dict):
    if actor.get("api_key_id"):
        own = Transaction.api_key_id == actor["api_key_id"]
        # §3.9: Playground runs are hidden from agent keys only; an application
        # key (e.g. a skill-building integration) must still read its own runs
        if actor.get("key_type") == "agent":
            return and_(own, Transaction.purpose != "test")
        return own
    if task_scope(actor) == "all":
        return Transaction.tenant_id == current_tenant()
    uid = actor.get("user_id")
    if not uid:
        return false()
    keys = select(ApiKey.id).where(ApiKey.tenant_id == current_tenant(),
                                   ApiKey.owner_user_id == uid)
    # New key submissions pin the account at submission. Older key tasks may
    # use their explicit key-owner relation; unknown/ownerless tasks are never
    # attributed from display names, assignments or previous review activity.
    return or_(Transaction.initiator_user_id == uid,
               and_(Transaction.initiator_user_id.is_(None),
                    Transaction.api_key_id.in_(keys)))


def visible_file_cond(actor: dict | None = None):
    actor = actor or current_actor()
    owner = _owner_cond(actor)
    txn = select(Transaction.id).where(
        Transaction.id == FileRecord.transaction_id,
        Transaction.tenant_id == current_tenant(),
        Transaction.status != "deleted", owner,
    ).correlate(FileRecord).exists()
    return and_(FileRecord.tenant_id == current_tenant(), txn)


def visible_txn_cond(actor: dict | None = None):
    return and_(Transaction.tenant_id == current_tenant(),
                Transaction.status != "deleted",
                Transaction.id.in_(select(FileRecord.transaction_id)
                                    .where(visible_file_cond(actor))))


async def require_file(s, file_id: str) -> FileRecord:
    row = (await s.execute(select(FileRecord).where(
        FileRecord.id == file_id, visible_file_cond()))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "file not found")
    return row


async def require_txn(s, txn_id: str) -> Transaction:
    row = (await s.execute(select(Transaction).where(
        Transaction.id == txn_id, visible_txn_cond()))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "transaction not found")
    return row
