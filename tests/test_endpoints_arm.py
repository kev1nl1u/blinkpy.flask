def test_arm_submits_priority_zero(app, logged_in_client):
    calls = []

    class FakeMod:
        async def async_arm(self, value): return value
    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None):
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
        def submit(self, priority, factory, key=None, timeout=None):
            calls.append(priority)
            return None
    app.extensions["blink_service"] = FakeService()

    res = logged_in_client.get("/api/status")
    assert res.status_code == 200
    assert 1 in calls  # network info submitted at priority 1
