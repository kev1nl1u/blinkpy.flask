"""Runtime patch for blinkpy 0.25.5 OAuth v2 2FA detection.

Blink's signin endpoint now returns HTTP 202 (with a `tsv_methods` body) to
signal that two-factor auth is required. Upstream blinkpy only recognizes 412,
so it misreads 202 as a generic failure ("Login failed") and never stores the
OAuth state needed to complete 2FA.

This module replaces `blinkpy.api.oauth_signin` with a version that treats 202
as 2FA_REQUIRED. `Auth._oauth_login_flow` calls the function via the module
attribute, so reassigning it here takes effect for the whole login flow.

Import this module once before any Blink login is attempted.
"""
import logging

from blinkpy import api
from blinkpy.api import OAUTH_SIGNIN_URL, OAUTH_USER_AGENT

_LOGGER = logging.getLogger(__name__)

# Statuses that mean "signed in, no 2FA" (mirrors upstream).
_SUCCESS_STATUSES = (301, 302, 303, 307, 308)


async def _oauth_signin_patched(auth, email, password, csrf_token):
    """Drop-in for api.oauth_signin that also accepts 202 as 2FA_REQUIRED."""
    headers = {
        "User-Agent": OAUTH_USER_AGENT,
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://api.oauth.blink.com",
        "Referer": OAUTH_SIGNIN_URL,
    }
    data = {
        "username": email,
        "password": password,
        "csrf-token": csrf_token,
    }

    response = await auth.session.post(
        OAUTH_SIGNIN_URL, headers=headers, data=data, allow_redirects=False
    )

    # 412 (legacy) and 202 (current) both mean two-factor verification required.
    if response.status in (412, 202):
        return "2FA_REQUIRED"
    if response.status in _SUCCESS_STATUSES:
        return "SUCCESS"

    _LOGGER.error("oauth_signin unexpected status %s", response.status)
    return None


def apply():
    """Install the patch (idempotent)."""
    if getattr(api.oauth_signin, "_blink202_patched", False):
        return
    _oauth_signin_patched._blink202_patched = True
    api.oauth_signin = _oauth_signin_patched
    _LOGGER.info("Applied blinkpy oauth_signin 202-as-2FA patch")
