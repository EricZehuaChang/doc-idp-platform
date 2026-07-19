"""App factory. Run: uvicorn --factory app.main:create_app --port 8200
Health probes per HA design (feasibility v2.0 §2.2): /healthz (live),
/readyz (dependencies OK).
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes import (auth, billing, data, hooks, process, review,
                            settings as settings_routes, skills)
from app.auth import security
from app.config import get_settings
from app.db import get_engine, init_db, session_factory
from app.models import Tenant, User
from app.tenancy import TenantMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    # bootstrap the default tenant (single-tenant "default" runs first, §11.7 M1)
    sf = session_factory()
    async with sf() as s:
        if await s.get(Tenant, settings.default_tenant) is None:
            s.add(Tenant(id=settings.default_tenant, name="Default Tenant"))
            await s.commit()
        # JWT secret must exist before the first login/verify (§11.9)
        await security.resolve_secret_key(s)
        # bootstrap admin: only when the user table is empty AND a password was
        # provided — never invent a default credential
        from sqlalchemy import func, select
        n_users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        if n_users == 0 and settings.admin_password:
            s.add(User(tenant_id=settings.default_tenant, email=settings.admin_email,
                       role="admin", unlimited=True,
                       password_hash=security.hash_password(settings.admin_password)))
            await s.commit()
            logging.getLogger("app.auth").info("bootstrap admin created: %s", settings.admin_email)
        elif n_users == 0 and settings.auth_mode == "on":
            logging.getLogger("app.auth").warning(
                "auth_mode=on but no users exist and IDP_ADMIN_PASSWORD is unset — no one can log in")
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Doc IDP Platform", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(TenantMiddleware)
    app.include_router(auth.router)
    app.include_router(settings_routes.router)
    app.include_router(process.router)
    app.include_router(skills.router)
    app.include_router(review.router)
    app.include_router(data.router)
    app.include_router(hooks.router)
    app.include_router(billing.router)

    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    async def readyz():
        sf = session_factory()
        async with sf() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready", "tier": get_settings().deploy_tier}

    # serve the built frontend if present (private-deploy pattern: one process)
    webdist = Path(__file__).resolve().parent / "webdist"
    if webdist.exists():
        app.mount("/", StaticFiles(directory=webdist, html=True), name="web")

    return app
