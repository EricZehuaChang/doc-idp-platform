"""Test-suite env insulation: a developer's local .env (e.g. IDP_AUTH_MODE=on
for manual acceptance testing) must never leak into the suite. Tests that WANT
auth-on set it explicitly via monkeypatch.setenv, which overrides this."""
import os

os.environ["IDP_AUTH_MODE"] = "off"
os.environ.pop("IDP_ADMIN_PASSWORD", None)
