"""Shadow billing (design v0.2 §12.6): from day one every completed file writes
an append-only meter entry — nothing is charged in M1. Three months of real
page/token data calibrate pricing before real charging (M2 freeze/charge flow).
Idempotency: one meter entry per file (idempotency_key = file id + stage).
"""
import logging

from sqlalchemy import select

from app.db import session_factory
from app.models import CreditLedger, FileRecord

log = logging.getLogger("idp.billing")

# placeholder rate card (platform config in M2, §12.1): credits per page by skill kind
DEFAULT_RATE_PER_PAGE = 1.0


async def shadow_meter(tenant_id: str, file_id: str, pages: int, usage: dict) -> None:
    sf = session_factory()
    key = f"meter:{file_id}"
    async with sf() as s:
        exists = (await s.execute(
            select(CreditLedger.id).where(CreditLedger.idempotency_key == key))).first()
        if exists:                      # replay-safe
            return
        f = await s.get(FileRecord, file_id)
        s.add(CreditLedger(
            tenant_id=tenant_id, kind="shadow_meter",
            amount=pages * DEFAULT_RATE_PER_PAGE, bucket="paid",
            transaction_id=f.transaction_id if f else None,   # dashboard per-skill rollup
            idempotency_key=key,
            note=(f"pages={pages} in={usage.get('prompt_tokens', 0)} "
                  f"out={usage.get('completion_tokens', 0)}")))
        await s.commit()
    log.info("shadow metered file=%s pages=%s", file_id, pages)
