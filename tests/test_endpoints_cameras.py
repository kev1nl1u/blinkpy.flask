def test_dashboard_renders(app, logged_in_client):
    res = logged_in_client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "toggleCameras()" in html
    assert "camera_panel" not in html  # include resolved
    assert "Telecamere" in html or "Cameras" in html


def test_cameras_endpoint(app, logged_in_client):
    class Cam:
        attributes = {
            "name": "Ingresso", "camera_id": "1", "sync_module": "mod",
            "temperature": 68, "temperature_calibrated": 71,
            "battery": "ok", "battery_voltage": 158, "wifi_strength": -52,
            "sync_signal_strength": 5, "motion_enabled": True,
            "last_record": "2026-09-10T22:10:00", "type": "owl", "version": "1.0",
        }
    calls = []
    class FakeService:
        started = True
        blink = type("B", (), {"cameras": {"Ingresso": Cam()}, "last_refresh": None})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            calls.append(label); return None
    app.extensions["blink_service"] = FakeService()

    res = logged_in_client.get("/api/cameras")
    assert res.status_code == 200
    cam = res.get_json()["cameras"][0]
    assert cam["name"] == "Ingresso"
    assert cam["temperature_c"] == 21.7   # calibrated 71F wins over raw 68F
    assert cam["battery"] == "ok"
    assert "refresh" in calls           # stale cache re-fetched


def test_cameras_endpoint_needs_connection(app, logged_in_client):
    app.extensions["blink_service"] = None
    assert logged_in_client.get("/api/cameras").status_code == 400
