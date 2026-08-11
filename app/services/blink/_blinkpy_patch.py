"""Runtime patches for blinkpy 0.25.5 OAuth v2 support.

Two upstream gaps are patched here:

1. 2FA detection. Blink's signin endpoint now returns HTTP 202 (with a
   `tsv_methods` body) to signal that two-factor auth is required. Upstream
   blinkpy only recognizes 412, so it misreads 202 as a generic failure
   ("Login failed") and never stores the OAuth state needed to complete 2FA.
   We replace `blinkpy.api.oauth_signin`; `Auth._oauth_login_flow` calls it
   via the module attribute, so reassigning it covers the whole login flow.

2. Access-token renewal. `Auth.startup()` renews via OAuth v2
   (`api.oauth_refresh_token`), but `Auth.query()` — the path taken once the
   4-hour access token expires while the process is running — calls
   `Auth.refresh_tokens(refresh=True)`, which posts the v2 refresh token to
   the *legacy* v1 login endpoint with the legacy client_id. That always
   fails, so every Blink call made after the token expires raises and the app
   looks connected while nothing works. We replace `Auth.refresh_tokens` with
   a version that renews over OAuth v2 and falls back to a full v2 login.

Import this module once before any Blink login is attempted.
"""
import logging

from blinkpy import api
from blinkpy.api import OAUTH_SIGNIN_URL, OAUTH_USER_AGENT
from blinkpy.auth import Auth, BlinkTwoFARequiredError, TokenRefreshFailed

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


async def _refresh_tokens_patched(self, refresh=False):
    """Drop-in for Auth.refresh_tokens that renews over OAuth v2.

    `refresh=False` (initial token acquisition) is left to upstream; only the
    renewal path is rewritten, since that is the one wired to the dead legacy
    endpoint.
    """
    if not refresh:
        return await _refresh_tokens_original(self, refresh=False)

    self.is_errored = True

    if self.refresh_token and self.hardware_id:
        try:
            token_data = await api.oauth_refresh_token(
                self, self.refresh_token, self.hardware_id
            )
        except Exception as error:  # network/transport failure
            _LOGGER.debug("OAuth v2 token refresh errored: %s", error)
            token_data = None
        if token_data:
            await self._process_token_data(token_data)
            self.is_errored = False
            _LOGGER.info("OAuth v2 token refresh successful")
            return True
        _LOGGER.warning("OAuth v2 refresh token rejected; retrying full login")

    # Refresh token expired or revoked: fall back to a full OAuth v2 login
    # with the stored username/password.
    try:
        success = await self._oauth_login_flow()
    except BlinkTwoFARequiredError:
        # Caller must collect a new 2FA code; leave is_errored set.
        raise
    except Exception as error:
        raise TokenRefreshFailed from error

    if not success:
        raise TokenRefreshFailed("OAuth v2 re-login failed")

    self.is_errored = False
    return True


_refresh_tokens_original = Auth.refresh_tokens


def apply():
    """Install the patches (idempotent)."""
    if not getattr(api.oauth_signin, "_blink202_patched", False):
        _oauth_signin_patched._blink202_patched = True
        api.oauth_signin = _oauth_signin_patched
        _LOGGER.info("Applied blinkpy oauth_signin 202-as-2FA patch")

    if not getattr(Auth.refresh_tokens, "_blinkv2_patched", False):
        _refresh_tokens_patched._blinkv2_patched = True
        Auth.refresh_tokens = _refresh_tokens_patched
        _LOGGER.info("Applied blinkpy OAuth v2 token-refresh patch")
