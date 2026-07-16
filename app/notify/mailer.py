"""Platform mailer (design v0.2 §11.8): SMTP settings are runtime-mutable
config, stored in platform_settings (NOT .env — .env is for deploy-time model
keys only). The SMTP password is Fernet-encrypted at rest with a key derived
from the platform JWT secret, and is never echoed back to any UI.

When SMTP is unconfigured the platform runs in "admin activation mode"
(§11.8 fallback): invite/reset endpoints answer 409 and the admin creates
accounts with an initial password + forced first-login change instead.
"""
import logging

from app.auth import security

log = logging.getLogger("idp.mailer")

SETTING_KEY = "smtp"          # platform_settings row
_SECURITY_MODES = ("ssl", "starttls", "none")


class MailerNotConfigured(RuntimeError):
    pass


# thin aliases: the Fernet primitives live in auth.security (shared with BYOK)
encrypt_password = security.encrypt_value
decrypt_password = security.decrypt_value


async def load_config(session) -> dict | None:
    from app.models import PlatformSetting
    row = await session.get(PlatformSetting, SETTING_KEY)
    return dict(row.value) if row else None


def public_view(cfg: dict | None) -> dict:
    """Config for the settings UI: password replaced by a has-password flag
    (write-only-no-echo rule, §11.8)."""
    if not cfg:
        return {"configured": False}
    out = {k: cfg.get(k, "") for k in
           ("host", "port", "security", "username", "from_addr", "from_name", "reply_to")}
    out["configured"] = bool(cfg.get("host"))
    out["has_password"] = bool(cfg.get("password_enc"))
    return out


async def send(session, to: str, subject: str, body: str) -> None:
    """Send one plain-text mail through the configured SMTP relay.
    Raises MailerNotConfigured when no usable config exists."""
    cfg = await load_config(session)
    if not cfg or not cfg.get("host"):
        raise MailerNotConfigured("SMTP is not configured")
    await _deliver(cfg, to, subject, body)


async def _deliver(cfg: dict, to: str, subject: str, body: str) -> None:
    """Actual SMTP delivery — split out so tests can stub the network edge."""
    import ssl
    from email.message import EmailMessage

    import aiosmtplib

    msg = EmailMessage()
    sender = cfg.get("from_addr") or cfg.get("username") or ""
    display = cfg.get("from_name") or ""
    msg["From"] = f"{display} <{sender}>" if display else sender
    msg["To"] = to
    msg["Subject"] = subject
    if cfg.get("reply_to"):
        msg["Reply-To"] = cfg["reply_to"]
    msg.set_content(body)

    security_mode = cfg.get("security", "starttls")
    kwargs: dict = {"hostname": cfg["host"], "port": int(cfg.get("port") or 587),
                    "timeout": 15}
    if security_mode == "ssl":
        kwargs["use_tls"] = True
        kwargs["tls_context"] = ssl.create_default_context()
    elif security_mode == "starttls":
        kwargs["start_tls"] = True
    if cfg.get("username"):
        kwargs["username"] = cfg["username"]
        kwargs["password"] = decrypt_password(cfg["password_enc"]) if cfg.get("password_enc") else ""
    await aiosmtplib.send(msg, **kwargs)
    log.info("mail sent to %s: %s", to, subject)
