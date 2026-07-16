"""Webhook events (design v0.2 §7 group "事件"): tenant-registered callbacks
fired on file lifecycle events, HMAC-SHA256 signed (X-IDP-Signature) so the
receiver can verify origin. Fire-and-forget with bounded retry; failures never
block the pipeline (delivery is at-most-once per attempt cycle, receivers must
be idempotent — same discipline we demand of ourselves).
"""
import hashlib
import hmac
import json
import logging

import httpx
from sqlalchemy import JSON, Boolean, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, session_factory
from app.models import _uuid

log = logging.getLogger("idp.webhooks")

EVENTS = ("file.completed", "file.pending_verification", "file.passed",
          "file.rejected", "file.error", "file.split")


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    secret: Mapped[str] = mapped_column(String(128), default="")
    events: Mapped[list] = mapped_column(JSON, default=list)   # empty = all
    active: Mapped[bool] = mapped_column(Boolean, default=True)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def fire(tenant_id: str, event: str, payload: dict,
               transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Deliver event to all matching hooks. Never raises."""
    sf = session_factory()
    async with sf() as s:
        hooks = (await s.execute(
            select(Webhook).where(Webhook.tenant_id == tenant_id,
                                  Webhook.active.is_(True)))).scalars().all()
    targets = [h for h in hooks if not h.events or event in h.events]
    if not targets:
        return
    body = json.dumps({"event": event, "data": payload}, ensure_ascii=False).encode()
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        for h in targets:
            headers = {"Content-Type": "application/json",
                       "X-IDP-Event": event}
            if h.secret:
                headers["X-IDP-Signature"] = _sign(h.secret, body)
            for attempt in range(2):
                try:
                    resp = await client.post(h.url, content=body, headers=headers)
                    if resp.status_code < 500:
                        break                       # 2xx/4xx: done (4xx = receiver's problem)
                except httpx.HTTPError as e:
                    log.warning("webhook %s attempt %s failed: %s", h.url, attempt + 1, e)
