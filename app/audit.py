"""Human operation audit; API activity has its own metadata-only ledger."""
from app.models import AuditLog
from app.tenancy import current_actor, current_tenant


def human_event(session, action: str, detail: dict) -> None:
    actor = current_actor()
    if not actor.get("api_key_id"):
        session.add(AuditLog(tenant_id=current_tenant(), actor=actor["name"],
                             action=action, detail=detail))
