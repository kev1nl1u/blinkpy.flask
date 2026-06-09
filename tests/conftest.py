import asyncio
import threading
import pytest


@pytest.fixture
def bg_loop():
    """A real asyncio loop running on a background thread (mirrors _BG_LOOP)."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)
    loop.close()
