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


# —— branded HTML template (Insavlo-style card: logo / title / message /
#    prominent action or code / footer notes). Inline styles only — email
#    clients strip <style> blocks. A plain-text part always rides along. ——

def render_email(*, brand: str, title: str, greeting: str, lines: list[str],
                 action_text: str | None = None, action_url: str | None = None,
                 footer_lines: list[str] | None = None) -> tuple[str, str]:
    """Returns (text_part, html_part). The text part carries the raw URL so
    HTML-blocking clients (and tests) still get a working link."""
    footer_lines = footer_lines or []
    text = "\n".join([f"{greeting}\n", *lines,
                      *( [f"\n{action_text}：{action_url}"] if action_url else [] ),
                      "", *footer_lines])

    paras = "".join(
        f'<p style="margin:0 0 14px;color:#333;font-size:15px;line-height:1.7;">{ln}</p>'
        for ln in lines)
    action = ""
    if action_url:
        action = (
            f'<div style="text-align:center;margin:28px 0;">'
            f'<a href="{action_url}" style="display:inline-block;background:#f0b429;'
            f'color:#1a1a1a;font-weight:700;font-size:15px;text-decoration:none;'
            f'padding:13px 36px;border-radius:8px;">{action_text}</a></div>'
            f'<p style="margin:0 0 14px;color:#999;font-size:12px;line-height:1.6;'
            f'word-break:break-all;">按钮无法点击时，请复制链接到浏览器打开：<br>{action_url}</p>')
    footer = "".join(
        f'<p style="margin:0 0 4px;color:#aaa;font-size:12px;line-height:1.6;">{ln}</p>'
        for ln in footer_lines)
    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 12px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
       style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;padding:40px 44px;font-family:'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif;">
<tr><td>
  <div style="font-size:26px;font-weight:800;color:#1a1a1a;letter-spacing:0.5px;margin-bottom:28px;">
    <span style="display:inline-block;width:14px;height:14px;background:#f0b429;border-radius:3px;margin-right:8px;"></span>{brand}</div>
  <h2 style="margin:0 0 22px;color:#1a1a1a;font-size:20px;font-weight:700;">{title}</h2>
  <p style="margin:0 0 14px;color:#333;font-size:15px;">{greeting}</p>
  {paras}
  {action}
  <hr style="border:none;border-top:1px solid #eee;margin:26px 0 18px;">
  {footer}
</td></tr></table>
</td></tr></table>
</body></html>"""
    return text, html


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


async def send(session, to: str, subject: str, body: str,
               html: str | None = None) -> None:
    """Send one mail (text + optional HTML alternative) through the configured
    SMTP relay. Raises MailerNotConfigured when no usable config exists."""
    cfg = await load_config(session)
    if not cfg or not cfg.get("host"):
        raise MailerNotConfigured("SMTP is not configured")
    await _deliver(cfg, to, subject, body, html)


async def send_templated(session, to: str, subject: str, *, title: str,
                         greeting: str, lines: list[str],
                         action_text: str | None = None,
                         action_url: str | None = None,
                         footer_lines: list[str] | None = None) -> None:
    """Branded card email; brand name follows the configured from_name."""
    cfg = await load_config(session)
    if not cfg or not cfg.get("host"):
        raise MailerNotConfigured("SMTP is not configured")
    brand = cfg.get("from_name") or "DOC·IDP"
    text, html = render_email(brand=brand, title=title, greeting=greeting,
                              lines=lines, action_text=action_text,
                              action_url=action_url, footer_lines=footer_lines)
    await _deliver(cfg, to, subject, text, html)


async def _deliver(cfg: dict, to: str, subject: str, body: str,
                   html: str | None = None) -> None:
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
    if html:
        msg.add_alternative(html, subtype="html")

    security_mode = cfg.get("security", "starttls")
    kwargs: dict = {"hostname": cfg["host"], "port": int(cfg.get("port") or 587),
                    "timeout": 30}
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
