from types import SimpleNamespace
from app.services.blink import bulk


def test_bulk_enqueues_manifest_then_clips(monkeypatch):
    submitted = []

    class FakeItem:
        def __init__(self, id): self.id = id
    class FakeMod:
        def __init__(self): self._local_storage = {"manifest": [FakeItem("a"), FakeItem("b")],
                                                   "last_manifest_id": "m1"}
        async def update_local_storage_manifest(self): return True
    fake_blink = SimpleNamespace(sync={"mod": FakeMod()})

    class FakeService:
        started = True
        blink = fake_blink
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            submitted.append((priority, key)); return None
        def submit_nowait(self, priority, factory, key=None, label=None):
            submitted.append((priority, key)); return None

    bulk.run_bulk_download(FakeService())
    assert (1, None) in submitted
    assert (3, "mod:a") in submitted
    assert (3, "mod:b") in submitted
