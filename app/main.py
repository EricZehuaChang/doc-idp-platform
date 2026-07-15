"""App factory. Run: uvicorn --factory app.main:create_app --port 8200
Health probes per HA design (feasibility v2.0 §2.2): /healthz (live),
/readyz (dependencies OK).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import process, skills
from app.config import get_settings
from app.db import get_engine, init_db, session_factory
from app.models import Tenant
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
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Doc IDP Platform", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(TenantMiddleware)
    app.include_router(process.router)
    app.include_router(skills.router)

    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    async def readyz():
        sf = session_factory()
        async with sf() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ready", "tier": get_settings().deploy_tier}

    return app
