import datetime as dt
from types import SimpleNamespace
import pytest
from app.services.blink import motion


def _item(id, when):
    return SimpleNamespace(id=id, name="Cam", created_at=when)


class FakeMod:
    def __init__(self, armed, manifest):
        self._armed = armed
        self._local_storage = {"manifest": manifest, "last_manifest_id": "m1"}
        self.network_info = {"network": {"armed": armed}}

    @property
    def arm(self):
        return self._armed

    async def get_network_info(self):
        return True

    async def update_local_storage_manifest(self):
        return True


class FakeService:
    started = True

    def __init__(self, mod):
        self.blink = SimpleNamespace(sync={"mod": mod})
        self.submitted = []
        self.enqueued = []

    def submit(self, priority, factory, key=None, timeout=None):
        self.submitted.append((priority, key))
        return None

    def submit_nowait(self, priority, factory, key=None):
        self.enqueued.append((priority, key))
        return None


@pytest.fixture(autouse=True)
def _reset_marks():
    motion._last_seen.clear()
    yield
    motion._last_seen.clear()


def test_disarmed_skips_manifest_and_downloads():
    t = dt.datetime(2026, 6, 18, 8, 0, tzinfo=dt.timezone.utc)
    svc = FakeService(FakeMod(armed=False, manifest=[_item("a", t)]))
    motion.run_motion_poll(svc)
    # Only the cheap armed check ran; no manifest build, no downloads.
    assert svc.submitted == [(1, None)]
    assert svc.enqueued == []


def test_first_armed_poll_seeds_without_downloading():
    t = dt.datetime(2026, 6, 18, 8, 0, tzinfo=dt.timezone.utc)
    svc = FakeService(FakeMod(armed=True, manifest=[_item("a", t)]))
    motion.run_motion_poll(svc)
    # Armed check + manifest build, but history is seeded, not downloaded.
    assert (1, None) in svc.submitted
    assert svc.enqueued == []
    assert motion._last_seen["mod"] == t


def test_second_poll_enqueues_only_new_clips():
    old = dt.datetime(2026, 6, 18, 8, 0, tzinfo=dt.timezone.utc)
    new = dt.datetime(2026, 6, 18, 8, 5, tzinfo=dt.timezone.utc)
    mod = FakeMod(armed=True, manifest=[_item("a", old)])
    svc = FakeService(mod)
    motion.run_motion_poll(svc)          # seeds at `old`
    mod._local_storage["manifest"] = [_item("a", old), _item("b", new)]
    motion.run_motion_poll(svc)          # only "b" is new
    assert (2, "mod:b") in svc.enqueued
    assert (2, "mod:a") not in svc.enqueued
    assert motion._last_seen["mod"] == new
