"""One-time account token service (§11.8): issue/consume invite & reset
tokens. Plain token leaves the process exactly once (inside the email); the
DB stores only its sha256. Resend is rate-limited (60s per user+purpose)."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import AuthToken, User

RESEND_COOLDOWN_S = 60
TTL_HOURS = 24


class RateLimited(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(session, user: User, purpose: str) -> str:
    """Create a fresh one-time token for the user. Raises RateLimited when the
    previous one for the same purpose is younger than the cooldown."""
    last = (await session.execute(
        select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose)
        .order_by(AuthToken.created_at.desc()).limit(1))).scalar_one_or_none()
    if last is not None:
        age = (_now() - last.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age < RESEND_COOLDOWN_S:
            raise RateLimited(f"retry in {int(RESEND_COOLDOWN_S - age)}s")
    token = secrets.token_urlsafe(32)
    session.add(AuthToken(tenant_id=user.tenant_id, user_id=user.id, email=user.email,
                          purpose=purpose, token_hash=_hash(token),
                          expires_at=_now() + timedelta(hours=TTL_HOURS)))
    await session.commit()
    return token


async def consume(session, token: str, purpose: str) -> User | None:
    """Validate + burn a token. Returns its user, or None (unknown/expired/
    already used). Caller commits together with its own state change so the
    burn and the effect are one transaction."""
    row = (await session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash(token),
                                AuthToken.purpose == purpose))).scalar_one_or_none()
    if row is None or row.used_at is not None:
        return None
    if row.expires_at.replace(tzinfo=timezone.utc) < _now():
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.active:
        return None
    row.used_at = _now()
    return user
