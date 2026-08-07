"""Test-suite env insulation: a developer's local .env (e.g. IDP_AUTH_MODE=on
for manual acceptance testing) must never leak into the suite. Tests that WANT
auth-on set it explicitly via monkeypatch.setenv, which overrides this."""
import os

os.environ["IDP_AUTH_MODE"] = "off"
# deploy tier steers default parser/detector resolution; a developer's .env
# with IDP_DEPLOY_TIER=standard would silently reroute detector tests
os.environ["IDP_DEPLOY_TIER"] = "lite"
os.environ.pop("IDP_ADMIN_PASSWORD", None)
