"""Webhook registration API (tenant-scoped)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select

from app.db import session_factory
from app.integrations.webhooks import EVENTS, Webhook
from app.tenancy import current_tenant

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


class HookCreate(BaseModel):
    url: HttpUrl
    secret: str = ""
    events: list[str] = []


@router.post("", status_code=201)
async def register(body: HookCreate):
    bad = [e for e in body.events if e not in EVENTS]
    if bad:
        raise HTTPException(400, f"unknown events: {bad}; valid: {list(EVENTS)}")
    sf = session_factory()
    async with sf() as s:
        hook = Webhook(tenant_id=current_tenant(), url=str(body.url),
                       secret=body.secret, events=body.events)
        s.add(hook)
        await s.commit()
        return {"id": hook.id, "url": hook.url, "events": hook.events or "all"}


@router.get("")
async def list_hooks():
    sf = session_factory()
    async with sf() as s:
        rows = (await s.execute(
            select(Webhook).where(Webhook.tenant_id == current_tenant()))).scalars().all()
        return [{"id": h.id, "url": h.url, "events": h.events or "all",
                 "active": h.active} for h in rows]


@router.delete("/{hook_id}")
async def remove(hook_id: str):
    sf = session_factory()
    async with sf() as s:
        h = await s.get(Webhook, hook_id)
        if h is None or h.tenant_id != current_tenant():
            raise HTTPException(404, "webhook not found")
        await s.delete(h)
        await s.commit()
    return {"deleted": hook_id}
