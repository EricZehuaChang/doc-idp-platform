"""Tenant context pipeline (design v0.2 §11.2, application-layer line of the
double defense; PG RLS is the DB-layer line added in M2).

M1 resolution: X-Tenant-Id header, else the default tenant. TODO(M1.5): resolve
tenant from JWT / API-Key claims once auth lands; header fallback then becomes
dev-mode only. Repositories must always filter by current_tenant().
"""
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import get_settings

_tenant_ctx: ContextVar[str] = ContextVar("tenant_id", default="default")


def current_tenant() -> str:
    return _tenant_ctx.get()


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant = request.headers.get("X-Tenant-Id") or get_settings().default_tenant
        token = _tenant_ctx.set(tenant)
        try:
            return await call_next(request)
        finally:
            _tenant_ctx.reset(token)
