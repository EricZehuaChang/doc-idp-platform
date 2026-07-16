"""Auth primitives (design v0.2 §11.9, fork-and-owned from KBase auth/security):
password hashing, session JWT, secret resolution, API-key hashing.

Deliberate departures from KBase:
- pbkdf2_hmac (stdlib, OWASP params) instead of passlib+bcrypt — passlib is
  unmaintained and its bcrypt pin breaks on fresh Windows venvs; zero-dep wins.
- JWT carries tenant_id: it is the trusted tenant source when auth is on
  (tenancy.py resolves context from it; X-Tenant-Id header becomes dev-only).
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt
from jwt import InvalidTokenError  # noqa: F401  re-export: callers except security.InvalidTokenError

ALGORITHM = "HS256"
SESSION_TOKEN_TTL_SECONDS = 7 * 24 * 3600

_PBKDF2_ITERATIONS = 600_000       # OWASP 2023+ recommendation for pbkdf2-sha256

# process-wide JWT secret, resolved once at app startup (see resolve_secret_key)
_secret: str | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _scheme, iters, salt_b64, dk_b64 = password_hash.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
    return hmac.compare_digest(dk, expected)


def create_session_token(*, user_id: str, tenant_id: str, role: str, email: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "tenant": tenant_id, "role": role, "email": email,
               "iat": now, "exp": now + SESSION_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, get_secret(), algorithm=ALGORITHM)


def decode_session_token(token: str) -> dict:
    """Expired/tampered/wrong-key all raise jwt.InvalidTokenError subclasses —
    callers need a single except."""
    return jwt.decode(token, get_secret(), algorithms=[ALGORITHM])


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


# —— symmetric encryption for runtime-mutable secrets at rest (SMTP password,
#    tenant BYOK provider keys). Key derived from the platform JWT secret. ——

def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(get_secret().encode()).digest())
    return Fernet(key)


def encrypt_value(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_value(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def get_secret() -> str:
    if _secret is None:
        raise RuntimeError("JWT secret not resolved yet — app startup must call resolve_secret_key")
    return _secret


async def resolve_secret_key(session) -> str:
    """env IDP_SECRET_KEY wins (explicit in production); otherwise generate once
    and persist to platform_settings so restarts don't invalidate live sessions
    (KBase pattern). Caches process-wide."""
    global _secret
    env_secret = os.environ.get("IDP_SECRET_KEY")
    if env_secret:
        _secret = env_secret
        return _secret
    from app.models import PlatformSetting

    row = await session.get(PlatformSetting, "secret_key")
    if row is None:
        row = PlatformSetting(key="secret_key", value={"v": secrets.token_urlsafe(32)})
        session.add(row)
        await session.commit()
    _secret = row.value["v"]
    return _secret
