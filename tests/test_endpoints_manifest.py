def test_remote_clips_route_exists(app, logged_in_client):
    res = logged_in_client.get("/api/blink/local/remote")
    # Route exists (not 404). 400 if Blink not connected is acceptable.
    assert res.status_code != 404


def test_remote_clips_lists_manifest(app, logged_in_client):
    import datetime
    class FakeItem:
        def __init__(self, id, name):
            self.id = id; self.name = name
            self.created_at = datetime.datetime(2026, 6, 9, 12, 0, 0)
    class FakeMod:
        _local_storage = {"manifest": [FakeItem("c1", "Front"), FakeItem("c2", "Back")]}
        async def update_local_storage_manifest(self): return True
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            assert priority == 1  # manifest refresh at priority 1
            return True
    app.extensions["blink_service"] = FakeService()
    res = logged_in_client.get("/api/blink/local/remote")
    assert res.status_code == 200
    clips = res.get_json()["clips"]
    ids = {c["id"] for c in clips}
    assert ids == {"c1", "c2"}
    assert all(c["module"] == "mod" for c in clips)
