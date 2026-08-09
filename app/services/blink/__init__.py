import asyncio
import atexit
import json
import threading
from pathlib import Path
from aiohttp import ClientSession
from blinkpy.blinkpy import Blink
from blinkpy.auth import Auth, BlinkTwoFARequiredError
from app.services.blink import _blinkpy_patch
from app.services.blink.queue import BlinkQueue

# Blink now signals 2FA with HTTP 202; blinkpy 0.25.5 only knows 412. Patch it.
_blinkpy_patch.apply()


CREDENTIALS_FILE = Path("credentials.json")


class BlinkService:
    """Manages Blink connection lifecycle for Flask app."""

    def __init__(self):
        self.session = None
        self.blink = None
        self.started = False
        self.awaiting_2fa = False

    def save_credentials(self):
        """Write current auth state (incl. rotated tokens) to credentials.json.

        Blink rotates the refresh token on every renewal, so the file must be
        rewritten after each one — otherwise the next process start tries a
        refresh token that Blink has already invalidated.
        """
        if not self.blink or not self.blink.auth:
            return
        try:
            tmp = CREDENTIALS_FILE.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(self.blink.auth.login_attributes, f)
            tmp.chmod(0o600)
            tmp.replace(CREDENTIALS_FILE)
        except Exception:
            # Never let a persistence failure break an in-flight Blink call.
            pass

    def _make_auth(self, credentials):
        """Build an Auth bound to a fresh session, persisting on token refresh."""
        self.session = ClientSession()
        self.blink = Blink()
        auth = Auth(credentials, session=self.session, callback=self.save_credentials)
        self.blink.auth = auth
        return auth

    async def login_with_credentials(self, email, password):
        """Login with provided email and password."""
        await self.stop()

        credentials = {"username": email, "password": password, "host": "prod"}
        self._make_auth(credentials)

        try:
            if not await self.blink.start():
                raise ValueError("Blink login failed")
            await self.blink.refresh(force=True)
            self.save_credentials()
            self.started = True
            self.awaiting_2fa = False
        except BlinkTwoFARequiredError:
            self.awaiting_2fa = True
            raise

        return self.blink

    async def start(self):
        """Initialize Blink connection from saved credentials.json."""
        if self.started:
            return self.blink

        if not CREDENTIALS_FILE.exists():
            raise ValueError("credentials.json not found")

        try:
            with open(CREDENTIALS_FILE, 'r') as f:
                creds = json.load(f)
        except (json.JSONDecodeError, IOError):
            raise ValueError("Invalid or empty credentials.json")

        self._make_auth(creds)

        try:
            # Blink.start() swallows LoginError/TokenRefreshFailed and returns
            # False; treating that as success leaves the app "connected" with
            # no sync modules, which surfaces as unknown status + failed arm.
            if not await self.blink.start():
                raise ValueError("Blink login failed — re-enter credentials")
            await self.blink.refresh(force=True)
            self.save_credentials()
            self.started = True
            self.awaiting_2fa = False
        except BlinkTwoFARequiredError:
            self.awaiting_2fa = True
            raise

        return self.blink

    async def send_2fa_code(self, code):
        """Complete login with 2FA code."""
        if not self.awaiting_2fa or not self.blink:
            raise ValueError("Not waiting for 2FA code")
        try:
            await self.blink.send_2fa_code(code)
            await self.blink.refresh(force=True)
            self.save_credentials()
            self.started = True
            self.awaiting_2fa = False
        except Exception:
            self.awaiting_2fa = True
            raise

    async def stop(self):
        """Cleanup Blink connection and session."""
        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass
        self.session = None
        self.blink = None
        self.started = False
        self.awaiting_2fa = False

    def submit(self, priority, factory, key=None, timeout=None, label=None):
        """Submit a Blink coroutine factory to the serialized priority queue."""
        return queue.submit(priority, factory, key=key, timeout=timeout, label=label)

    def submit_nowait(self, priority, factory, key=None, label=None):
        return queue.submit_nowait(priority, factory, key=key, label=label)

    def reprioritize(self, key, new_priority):
        queue.reprioritize(key, new_priority)

    def queue_snapshot(self):
        """Return the serialized priority queue's running/pending state."""
        return queue.snapshot()

    def clear_queue(self):
        """Cancel all pending queued jobs. Returns the number removed."""
        return queue.clear()

    def start_from_credentials(self):
        """Synchronous wrapper used by the scheduler thread."""
        from app.services.blink import run_sync
        return run_sync(self.start())


service = BlinkService()

# Dedicated asyncio loop on a background thread so the aiohttp ClientSession
# stays alive for the lifetime of the Flask process.
_BG_LOOP = asyncio.new_event_loop()

def _run_loop():
    asyncio.set_event_loop(_BG_LOOP)
    _BG_LOOP.run_forever()

_LOOP_THREAD = threading.Thread(target=_run_loop, daemon=True)
_LOOP_THREAD.start()

queue = BlinkQueue(_BG_LOOP)


def run_sync(coro, timeout=None):
    """Submit a coroutine to the background loop and block until done."""
    future = asyncio.run_coroutine_threadsafe(coro, _BG_LOOP)
    return future.result(timeout)


def _cleanup():
    try:
        run_sync(service.stop(), timeout=10)
    except Exception:
        pass
    try:
        _BG_LOOP.call_soon_threadsafe(_BG_LOOP.stop)
    except Exception:
        pass

atexit.register(_cleanup)
