"""Celery task wrappers around the runner stages. Tasks are sync (Celery
contract); each runs its stage coroutine in a fresh event loop with the tenant
contextvar pinned (RLS GUC + repository filters both key off it, §11.2/§11.4).

Failure semantics (§4.3): business failures land in the DB (file status=error)
and the canvas continues — parse/extract never raise across the broker, so one
bad file can't break the chord that finalizes its transaction.
"""
import asyncio
import logging

from celery import chain, chord

from app.tasks import runner
from app.tasks.celery_app import celery_app, parse_queue_for
from app.tenancy import _tenant_ctx

log = logging.getLogger("idp.celery")


def _run(tenant: str, coro):
    """Run a stage coroutine under the tenant context. Engine is disposed after
    each task: asyncio.run creates a fresh loop, and pooled asyncpg connections
    must not survive into the next task's loop (classic cross-loop bug)."""
    async def _wrapped():
        tok = _tenant_ctx.set(tenant)
        try:
            return await coro
        finally:
            _tenant_ctx.reset(tok)
            from app.db import get_engine
            await get_engine().dispose()
    return asyncio.run(_wrapped())


@celery_app.task(name="idp.run_transaction")
def run_transaction(transaction_id: str, tenant: str) -> None:
    """Orchestrator: plan in DB, then fan files out as parse->extract chains
    gathered by a finalize chord."""
    plan = _run(tenant, runner.plan_transaction(transaction_id))
    if plan is None:
        return
    pkg, file_ids = plan
    pkg_dict = pkg.model_dump()
    q = parse_queue_for(pkg.parser)
    expects_tables = runner.skill_expects_tables(pkg)
    header = [
        chain(parse_file.si(fid, pkg.parser, tenant, expects_tables).set(queue=q),
              extract_file.si(fid, pkg_dict, tenant))
        for fid in file_ids
    ]
    chord(header)(finalize_transaction.si(transaction_id, tenant))


@celery_app.task(name="idp.parse_file")
def parse_file(file_id: str, parser_pin: str | None, tenant: str,
               expects_tables: bool = False) -> str:
    # expects_tables defaults False so messages queued by an older publisher
    # still deserialize; they just skip the table-escalation rule once.
    try:
        _run(tenant, runner.parse_stage(file_id, parser_pin, expects_tables))
    except Exception as e:
        log.exception("parse failed for %s", file_id)
        _run(tenant, runner.mark_error(file_id, str(e)[:500]))
    return file_id


@celery_app.task(name="idp.extract_file")
def extract_file(file_id: str, pkg_dict: dict, tenant: str) -> str:
    from app.skillengine.schema import SkillPackage
    try:
        _run(tenant, runner.extract_stage(file_id, SkillPackage(**pkg_dict)))
    except Exception as e:
        log.exception("extract failed for %s", file_id)
        _run(tenant, runner.mark_error(file_id, str(e)[:500]))
    return file_id


@celery_app.task(name="idp.finalize_transaction")
def finalize_transaction(transaction_id: str, tenant: str) -> None:
    _run(tenant, runner.finalize_transaction(transaction_id))


@celery_app.task(name="idp.ping")
def ping() -> str:
    """Broker/worker liveness probe used by tests and /readyz later."""
    return "pong"
