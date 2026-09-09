"""Request context pipeline: authentication + tenant resolution (design v0.2
§11.2 application-layer line of the double defense; PG RLS is the DB-layer line).

Two modes (IDP_AUTH_MODE):
- "off" (lite/dev default, M1 behavior): X-Tenant-Id header else default tenant;
  actor is a synthetic admin so role checks are no-ops and audit rows still say
  who ("anonymous") acted.
- "on"  (enterprise): /api/* requires a Bearer credential — session JWT (users)
  or API key (integrations). Tenant comes from the credential, NEVER from the
  header (a client-chosen tenant header would be a cross-tenant hole).

Repositories must always filter by current_tenant().
"""
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings

_tenant_ctx: ContextVar[str] = ContextVar("tenant_id", default="default")
_actor_ctx: ContextVar[dict] = ContextVar("actor", default={"name": "anonymous", "role": "admin",
                                                            "user_id": None})

# paths under /api that must stay reachable without a credential
# (account entry flows §11.8/§11.9: the token in the flow is the credential)
_PUBLIC_API_PATHS = {"/api/v1/auth/login", "/api/v1/auth/activate",
                     "/api/v1/auth/forgot", "/api/v1/auth/reset",
                     "/api/v1/auth/oidc/enabled", "/api/v1/auth/oidc/login",
                     "/api/v1/auth/oidc/callback"}

_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}


def current_tenant() -> str:
    return _tenant_ctx.get()


def current_actor() -> dict:
    """{"name", "role", "user_id"} — audit trail + role checks read this."""
    return _actor_ctx.get()


@contextmanager
def as_tenant(tenant_id: str):
    """Platform-operator write scope (§12.4 manual billing ops): sessions
    opened inside run with the RLS GUC pinned to the TARGET tenant, so a
    cross-tenant topup/gift insert passes the WITH CHECK policy. Use only for
    audited platform actions — never to read tenant business data (support
    access has its own authorization trail, §11.10 rule 2)."""
    token = _tenant_ctx.set(tenant_id)
    try:
        yield
    finally:
        _tenant_ctx.reset(token)


def has_role(min_role: str) -> bool:
    """Role-rank check (admin > operator > viewer). Unknown roles rank below
    everything: legacy/bogus rows deny instead of KeyError-500."""
    return _ROLE_RANK.get(current_actor()["role"], -1) >= _ROLE_RANK[min_role]


def _unauthorized(detail: str = "authentication required") -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail},
                        headers={"WWW-Authenticate": "Bearer"})


async def _resolve_bearer(token: str) -> tuple[str, dict] | None:
    """Try session JWT first, then API key. Returns (tenant_id, actor) or None.
    Both hit the DB so disable/revoke takes effect on the next request."""
    from app.auth import security
    from app.db import session_factory
    from app.models import ApiKey, User

    sf = session_factory()
    try:
        payload = security.decode_session_token(token)
    except security.InvalidTokenError:
        payload = None
    if payload is not None:
        async with sf() as s:
            user = await s.get(User, payload.get("sub"))
        # a password reset bumps session_epoch: tokens minted before it (missing
        # claim = epoch 0) no longer resolve — the reset really cuts old
        # sessions, including one an attacker may still hold
        if user is None or not user.active \
                or payload.get("ep", 0) != user.session_epoch:
            return None
        return user.tenant_id, {"name": user.email, "role": user.role, "user_id": user.id,
                                "unlimited": user.unlimited}  # Owner Root flag (§12.7)

    # API-key channel (API-first design §7): opaque key, sha256 lookup
    from sqlalchemy import select
    key_hash = security.hash_api_key(token)
    async with sf() as s:
        row = (await s.execute(select(ApiKey).where(ApiKey.key_hash == key_hash,
                                                    ApiKey.active))).scalar_one_or_none()
    if row is None:
        return None
    # API keys act as operator: they process documents, they don't manage users.
    # Key identity rides along so the billing gate can charge an allocated
    # key's own budget instead of the tenant pool (§12.7 quota modes).
    return row.tenant_id, {"name": f"apikey:{row.name or row.id}", "role": "operator",
                           "user_id": None, "api_key_id": row.id,
                           "quota_mode": row.quota_mode}


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path

        if settings.auth_mode == "on" and path.startswith("/api/") \
                and path not in _PUBLIC_API_PATHS:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return _unauthorized()
            resolved = await _resolve_bearer(auth_header[len("Bearer "):])
            if resolved is None:
                return _unauthorized("invalid or expired credential")
            tenant, actor = resolved
        else:
            # off mode / public path: M1 dev behavior, synthetic admin actor
            tenant = request.headers.get("X-Tenant-Id") or settings.default_tenant
            actor = {"name": "anonymous", "role": "admin", "user_id": None}

        t_token = _tenant_ctx.set(tenant)
        a_token = _actor_ctx.set(actor)
        try:
            return await call_next(request)
        finally:
            _tenant_ctx.reset(t_token)
            _actor_ctx.reset(a_token)
