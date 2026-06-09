def test_get_settings_returns_config(app, logged_in_client, tmp_path):
    from app.services.settings import Settings
    app.extensions["settings"] = Settings(tmp_path / "s.json")
    res = logged_in_client.get("/api/settings")
    assert res.status_code == 200
    assert "scheduled_download" in res.get_json()


def test_post_settings_validates_and_reschedules(app, logged_in_client, tmp_path):
    from app.services.settings import Settings
    rescheduled = []
    class FakeSched:
        def apply(self, cfg): rescheduled.append(cfg)
    app.extensions["settings"] = Settings(tmp_path / "s.json")
    app.extensions["scheduler"] = FakeSched()
    res = logged_in_client.post("/api/settings", json={
        "scheduled_download": {"enabled": True, "time": "04:00", "timezone": "UTC"}
    })
    assert res.status_code == 200
    assert rescheduled  # scheduler reapplied


def test_post_settings_rejects_bad_time(app, logged_in_client, tmp_path):
    from app.services.settings import Settings
    app.extensions["settings"] = Settings(tmp_path / "s.json")
    app.extensions["scheduler"] = type("S", (), {"apply": lambda self, c: None})()
    res = logged_in_client.post("/api/settings", json={
        "scheduled_download": {"enabled": True, "time": "99:99", "timezone": "UTC"}
    })
    assert res.status_code == 400


def test_boost_reprioritizes_and_enqueues(app, logged_in_client):
    calls = {"reprio": [], "submit": []}
    class FakeItem:
        id = "c1"; name = "Front"
        import datetime as _dt
        created_at = _dt.datetime(2026, 6, 9)
    class FakeMod:
        _local_storage = {"manifest": [FakeItem()], "last_manifest_id": "m1"}
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def reprioritize(self, key, prio): calls["reprio"].append((key, prio))
        def submit_nowait(self, prio, factory, key=None): calls["submit"].append((prio, key))
    app.extensions["blink_service"] = FakeService()
    res = logged_in_client.post("/api/blink/local/clip/boost", json={"module": "mod", "clip_id": "c1"})
    assert res.status_code == 200
    assert ("mod:c1", 2) in calls["reprio"]
    assert (2, "mod:c1") in calls["submit"]
