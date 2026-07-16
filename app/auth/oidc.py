"""OIDC SSO — authorization-code flow with JIT provisioning (design v0.2
§11.9, M2 tier: JIT; SCIM/directory sync land M3). No IdP SDK: discovery +
token exchange + userinfo are three plain HTTP calls (httpx), which keeps the
dependency surface zero and works with Keycloak/Azure AD/Okta/Authing alike.

Identity rule (§11.9): external_id (IdP sub) is the hard link; email is only
the first-match anchor so an IdP-side email change never duplicates accounts.
Config lives in platform_settings ("oidc"), client_secret encrypted at rest.
"""
import logging
import time

import httpx
import jwt as pyjwt

from app.auth import security
from app.config import get_settings

log = logging.getLogger("idp.oidc")

SETTING_KEY = "oidc"
STATE_TTL_S = 600

# test seam: MockTransport in tests; None = real network
_transport: httpx.BaseTransport | None = None

# discovery cache: issuer -> (expires_epoch, doc)
_discovery: dict[str, tuple[float, dict]] = {}


async def load_config(session) -> dict | None:
    from app.models import PlatformSetting
    row = await session.get(PlatformSetting, SETTING_KEY)
    return dict(row.value) if row else None


def public_view(cfg: dict | None) -> dict:
    if not cfg:
        return {"enabled": False}
    return {"enabled": bool(cfg.get("enabled")), "issuer": cfg.get("issuer", ""),
            "client_id": cfg.get("client_id", ""),
            "has_secret": bool(cfg.get("client_secret_enc"))}


async def discover(issuer: str) -> dict:
    """OIDC discovery document, cached 10 minutes."""
    now = time.time()
    hit = _discovery.get(issuer)
    if hit and hit[0] > now:
        return hit[1]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(transport=_transport, timeout=15) as client:
        doc = (await client.get(url)).raise_for_status().json()
    _discovery[issuer] = (now + 600, doc)
    return doc


def make_state() -> str:
    """CSRF state as a short-lived signed token — stateless, single purpose."""
    now = int(time.time())
    return pyjwt.encode({"purpose": "oidc_state", "iat": now, "exp": now + STATE_TTL_S},
                        security.get_secret(), algorithm="HS256")


def check_state(state: str) -> bool:
    try:
        payload = pyjwt.decode(state, security.get_secret(), algorithms=["HS256"])
        return payload.get("purpose") == "oidc_state"
    except pyjwt.InvalidTokenError:
        return False


def redirect_uri() -> str:
    import os
    base = os.environ.get("IDP_PUBLIC_URL", "http://127.0.0.1:8200").rstrip("/")
    return f"{base}/api/v1/auth/oidc/callback"


async def build_login_url(cfg: dict) -> str:
    doc = await discover(cfg["issuer"])
    q = httpx.QueryParams({
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri(),
        "scope": "openid email profile",
        "state": make_state(),
    })
    return f"{doc['authorization_endpoint']}?{q}"


async def exchange_code(cfg: dict, code: str) -> dict:
    """code -> tokens -> userinfo. Returns {sub, email, name}."""
    doc = await discover(cfg["issuer"])
    secret = security.decrypt_value(cfg["client_secret_enc"]) \
        if cfg.get("client_secret_enc") else ""
    async with httpx.AsyncClient(transport=_transport, timeout=20) as client:
        tok = (await client.post(doc["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri(),
            "client_id": cfg["client_id"], "client_secret": secret,
        })).raise_for_status().json()
        ui = (await client.get(doc["userinfo_endpoint"], headers={
            "Authorization": f"Bearer {tok['access_token']}",
        })).raise_for_status().json()
    return {"sub": str(ui.get("sub") or ""), "email": ui.get("email") or "",
            "name": ui.get("name") or ""}


async def jit_user(session, ident: dict):
    """external_id hard-link first; email anchor second (binds sub to the
    existing account); JIT-create last. Returns an active User or None."""
    from sqlalchemy import select

    from app.models import User

    if not ident["sub"]:
        return None
    user = (await session.execute(
        select(User).where(User.auth_provider == "oidc",
                           User.external_id == ident["sub"]))).scalar_one_or_none()
    if user is None and ident["email"]:
        anchor = (await session.execute(
            select(User).where(User.email == ident["email"]))).scalar_one_or_none()
        if anchor is not None:
            anchor.auth_provider = "oidc"
            anchor.external_id = ident["sub"]
            user = anchor
    if user is None:
        if not ident["email"]:
            return None
        user = User(tenant_id=get_settings().default_tenant, email=ident["email"],
                    role="operator", auth_provider="oidc", external_id=ident["sub"],
                    email_verified=True)
        session.add(user)
        await session.flush()
        log.info("OIDC JIT user created: %s", ident["email"])
    return user if user.active else None
