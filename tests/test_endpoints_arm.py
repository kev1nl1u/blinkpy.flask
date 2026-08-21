def test_arm_submits_priority_zero(app, logged_in_client):
    calls = []

    class FakeMod:
        async def async_arm(self, value): return value
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            calls.append(priority)
            return True
    app.extensions["blink_service"] = FakeService()

    res = logged_in_client.post("/api/blink/arm")
    assert res.status_code == 200
    assert 0 in calls  # arm submitted at priority 0


def test_status_submits_priority_one(app, logged_in_client):
    calls = []

    class FakeMod:
        arm = True
        async def get_network_info(self): return None
    class FakeService:
        started = True
        awaiting_2fa = False
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            calls.append(priority)
            return None
    app.extensions["blink_service"] = FakeService()

    res = logged_in_client.get("/api/status")
    assert res.status_code == 200
    assert 1 in calls  # network info submitted at priority 1


def _fake_settings(tmp_path):
    from app.services.settings import Settings
    return Settings(tmp_path / "settings.json")


def test_auto_rearm_post_get_delete_roundtrip(app, logged_in_client, tmp_path):
    app.extensions["settings"] = _fake_settings(tmp_path)
    app.extensions["scheduler"] = None

    res = logged_in_client.post("/api/blink/auto-rearm", json={"minutes": 30})
    assert res.status_code == 200
    at = res.get_json()["auto_rearm"]["at"]
    assert at

    res = logged_in_client.get("/api/blink/auto-rearm")
    assert res.get_json()["auto_rearm"]["at"] == at
    assert res.get_json()["auto_rearm"]["seconds_remaining"] > 1700

    res = logged_in_client.delete("/api/blink/auto-rearm")
    assert res.get_json()["auto_rearm"]["at"] is None


def test_auto_rearm_rejects_invalid_body(app, logged_in_client, tmp_path):
    app.extensions["settings"] = _fake_settings(tmp_path)
    app.extensions["scheduler"] = None
    res = logged_in_client.post("/api/blink/auto-rearm", json={"minutes": 0})
    assert res.status_code == 400
    res = logged_in_client.post("/api/blink/auto-rearm", json={})
    assert res.status_code == 400


def test_manual_arm_cancels_pending_rearm(app, logged_in_client, tmp_path):
    class FakeMod:
        async def async_arm(self, value): return value
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            return True

    app.extensions["blink_service"] = FakeService()
    app.extensions["settings"] = _fake_settings(tmp_path)
    app.extensions["scheduler"] = None

    logged_in_client.post("/api/blink/auto-rearm", json={"minutes": 30})
    res = logged_in_client.post("/api/blink/arm")
    assert res.status_code == 200
    assert res.get_json()["auto_rearm"]["at"] is None


def test_disarm_keeps_pending_rearm(app, logged_in_client, tmp_path):
    class FakeMod:
        async def async_arm(self, value): return value
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            return True

    app.extensions["blink_service"] = FakeService()
    app.extensions["settings"] = _fake_settings(tmp_path)
    app.extensions["scheduler"] = None

    logged_in_client.post("/api/blink/auto-rearm", json={"minutes": 30})
    res = logged_in_client.post("/api/blink/disarm")
    assert res.status_code == 200
    assert res.get_json()["auto_rearm"]["at"] is not None
