import asyncio

import pytest
from blinkpy import api
from blinkpy.auth import Auth, BlinkTwoFARequiredError, TokenRefreshFailed

from app.services.blink import _blinkpy_patch

_blinkpy_patch.apply()


def _auth():
    auth = Auth(
        {"username": "u", "password": "p", "host": "prod"},
        no_prompt=True,
        session=object(),
    )
    auth.refresh_token = "rt-old"
    auth.hardware_id = "HW"
    auth.token = "at-old"
    return auth


def test_refresh_uses_oauth_v2_endpoint(monkeypatch):
    """Renewal must go through OAuth v2, not the dead legacy login endpoint."""
    seen = {}

    async def fake_oauth_refresh(auth, refresh_token, hardware_id):
        seen["refresh_token"] = refresh_token
        seen["hardware_id"] = hardware_id
        return {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 14400}

    async def fail_login(*args, **kwargs):
        pytest.fail("legacy login endpoint must not be used for refresh")

    monkeypatch.setattr(api, "oauth_refresh_token", fake_oauth_refresh)
    monkeypatch.setattr(api, "request_login", fail_login)

    auth = _auth()
    assert asyncio.run(auth.refresh_tokens(refresh=True)) is True
    assert seen == {"refresh_token": "rt-old", "hardware_id": "HW"}
    assert auth.token == "at-new"
    assert auth.refresh_token == "rt-new"
    assert auth.is_errored is False


def test_refresh_falls_back_to_full_login(monkeypatch):
    """A rejected refresh token triggers a full OAuth v2 login."""
    async def fake_oauth_refresh(auth, refresh_token, hardware_id):
        return None

    called = {}

    async def fake_flow(self):
        called["flow"] = True
        self.token = "at-relogin"
        return True

    monkeypatch.setattr(api, "oauth_refresh_token", fake_oauth_refresh)
    monkeypatch.setattr(Auth, "_oauth_login_flow", fake_flow)

    auth = _auth()
    assert asyncio.run(auth.refresh_tokens(refresh=True)) is True
    assert called["flow"] is True
    assert auth.token == "at-relogin"


def test_refresh_propagates_2fa_requirement(monkeypatch):
    """2FA must surface so the UI can prompt, not be masked as a generic failure."""
    async def fake_oauth_refresh(auth, refresh_token, hardware_id):
        return None

    async def fake_flow(self):
        raise BlinkTwoFARequiredError

    monkeypatch.setattr(api, "oauth_refresh_token", fake_oauth_refresh)
    monkeypatch.setattr(Auth, "_oauth_login_flow", fake_flow)

    with pytest.raises(BlinkTwoFARequiredError):
        asyncio.run(_auth().refresh_tokens(refresh=True))


def test_refresh_raises_when_relogin_fails(monkeypatch):
    async def fake_oauth_refresh(auth, refresh_token, hardware_id):
        return None

    async def fake_flow(self):
        return False

    monkeypatch.setattr(api, "oauth_refresh_token", fake_oauth_refresh)
    monkeypatch.setattr(Auth, "_oauth_login_flow", fake_flow)

    with pytest.raises(TokenRefreshFailed):
        asyncio.run(_auth().refresh_tokens(refresh=True))
