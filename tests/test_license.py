"""Service switch (app/license.py): off by default; when on, only a valid,
unexpired license issued after the last suspension lets requests through."""
import base64
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import license as lic
from app.storage import get_storage
from scripts.issue_license import sign_license
from tests.test_wp5_fast_and_playground import _boot


@pytest.fixture
def key(monkeypatch):
    """Throwaway keypair: the test signs with it and the verifier trusts it."""
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
    monkeypatch.setattr(lic, "_PUBLIC_KEY_B64", base64.b64encode(raw).decode())
    lic.reset_cache()
    yield private
    lic.reset_cache()


def _issue(key, expires=None, issued_at=None):
    expires = expires or (date.today() + timedelta(days=30)).isoformat()
    return json.dumps(sign_license("中银保", expires, key, issued_at=issued_at))


async def _states(client):
    """(api status, page status, probe status) as the gate sees them now."""
    lic.reset_cache()
    api = await client.get("/api/v1/formats")
    page = await client.get("/")
    probe = await client.get("/healthz")
    return api.status_code, page.status_code, probe.status_code, api


async def test_switch_off_by_default_keeps_existing_behaviour(tmp_path, monkeypatch, key):
    async for client, _ in _boot(tmp_path, monkeypatch):
        api, page, probe, _resp = await _states(client)
        assert (api, probe) == (200, 200)
        assert page != 503


async def test_locked_without_license_but_probes_stay_open(tmp_path, monkeypatch, key):
    monkeypatch.setenv("IDP_LICENSE_REQUIRED", "true")
    async for client, _ in _boot(tmp_path, monkeypatch):
        api, page, probe, resp = await _states(client)
        assert (api, page, probe) == (503, 503, 200)
        assert resp.json()["license_state"] == "missing"
        assert (await client.get("/readyz")).status_code == 200


async def test_valid_license_opens_and_suspend_closes(tmp_path, monkeypatch, key):
    monkeypatch.setenv("IDP_LICENSE_REQUIRED", "true")
    async for client, _ in _boot(tmp_path, monkeypatch):
        first = _issue(key, issued_at="2026-11-18T08:00:00+00:00")
        assert lic.install(first)["state"] == "valid"
        api, page, _probe, _resp = await _states(client)
        assert (api, page) == (200, 200)

        lic.suspend(datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc))
        api, page, _probe, resp = await _states(client)
        assert (api, page) == (503, 503)
        assert resp.json()["license_state"] == "suspended"

        # re-installing the pre-suspension license must not reopen the service
        assert lic.install(first)["state"] == "suspended"
        assert (await _states(client))[0] == 503

        reopened = _issue(key, issued_at="2026-11-18T10:00:00+00:00")
        assert lic.install(reopened)["state"] == "valid"
        assert (await _states(client))[0] == 200


async def test_expired_and_tampered_licenses_stay_locked(tmp_path, monkeypatch, key):
    monkeypatch.setenv("IDP_LICENSE_REQUIRED", "true")
    async for client, _ in _boot(tmp_path, monkeypatch):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert lic.install(_issue(key, expires=yesterday))["state"] == "expired"

        tampered = json.loads(_issue(key))
        tampered["expires"] = "2099-12-31"
        assert lic.install(json.dumps(tampered))["state"] == "invalid"
        assert lic.install("not json")["state"] == "invalid"
        # rejected installs never wrote a license file
        assert not get_storage().exists("license/license.json")
        assert (await _states(client))[0] == 503

        # a hand-edited license file on disk is caught by the gate too
        get_storage().put_bytes("license/license.json", json.dumps(tampered).encode())
        api, _page, _probe, resp = await _states(client)
        assert api == 503 and resp.json()["license_state"] == "invalid"


async def test_cli_exit_codes(tmp_path, monkeypatch, key, capsys):
    monkeypatch.setenv("IDP_DATA_DIR", str(tmp_path))
    import app.config as config
    config.get_settings.cache_clear()
    f = tmp_path / "license.json"
    f.write_text(_issue(key), encoding="utf-8")
    assert lic.main(["status"]) == 1                 # nothing installed yet
    assert lic.main(["install", str(f)]) == 0
    assert lic.main(["status"]) == 0
    assert lic.main(["suspend"]) == 0
    assert lic.main(["status"]) == 1
    assert "服务已暂停" in capsys.readouterr().out
    assert lic.main(["bogus"]) == 2
