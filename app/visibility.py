"""One authorization boundary for task reads, aggregates and review writes.

Assignment, the shared review queue (operators see every task awaiting review)
and "reviewed by me" grant access to one root task and its split documents,
never to unrelated uploads in the same transaction. Keys use immutable IDs exclusively.
"""
from fastapi import HTTPException
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import aliased

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
    return or_(Transaction.initiator_user_id == uid, Transaction.api_key_id.in_(keys))


def _same_task(cond):
    """EXISTS a file in the same root task (root + split children) matching
    ``cond(alias)``; correlated to the outer FileRecord."""
    g = aliased(FileRecord)
    return select(g.id).where(
        g.tenant_id == current_tenant(),
        g.transaction_id == FileRecord.transaction_id,
        func.coalesce(g.parent_file_id, g.id)
        == func.coalesce(FileRecord.parent_file_id, FileRecord.id),
        cond(g),
    ).correlate(FileRecord).exists()


def visible_file_cond(actor: dict | None = None):
    actor = actor or current_actor()
    owner = _owner_cond(actor)
    if task_scope(actor) == "own" and actor.get("user_id"):
        # D1: tasks assigned to me
        owner = or_(owner, _same_task(lambda g: g.assignee == actor["name"]))
        if actor.get("role") == "operator":
            # 2026-09-23 (Eric, option 2): the review queue is shared by all
            # operators — any production task still awaiting review is visible,
            # and a task I reviewed stays visible after my decision
            owner = or_(owner, and_(
                Transaction.purpose != "test",
                _same_task(lambda g: or_(g.status == "pending_verification",
                                         g.verified_by == actor["name"]))))
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
