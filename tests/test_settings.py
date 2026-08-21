import pytest
from app.services.settings import Settings, DEFAULTS


def test_load_defaults_when_absent(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.load() == DEFAULTS


def test_save_load_roundtrip(tmp_path):
    s = Settings(tmp_path / "settings.json")
    cfg = {
        "scheduled_download": {"enabled": True, "time": "04:30", "timezone": "Europe/Rome"},
        "motion_download": {"enabled": True, "interval_minutes": 3},
    }
    s.save(cfg)
    # load() backfills sections the saved file never mentioned.
    assert s.load() == {**cfg, "auto_rearm": {"at": None}}


def test_validate_rejects_bad_interval(tmp_path):
    s = Settings(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="interval_minutes"):
        s.validate({"motion_download": {"enabled": True, "interval_minutes": 0}})


def test_motion_download_in_defaults(tmp_path):
    s = Settings(tmp_path / "settings.json")
    md = s.load()["motion_download"]
    assert md == {"enabled": False, "interval_minutes": 2}


def test_validate_rejects_bad_time(tmp_path):
    s = Settings(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="time"):
        s.validate({"scheduled_download": {"enabled": True, "time": "25:99", "timezone": "Europe/Rome"}})


def test_validate_rejects_bad_timezone(tmp_path):
    s = Settings(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="timezone"):
        s.validate({"scheduled_download": {"enabled": True, "time": "04:00", "timezone": "Mars/Phobos"}})


def test_validate_accepts_valid(tmp_path):
    s = Settings(tmp_path / "settings.json")
    cfg = {"scheduled_download": {"enabled": False, "time": "23:00", "timezone": "UTC"}}
    s.validate(cfg)  # must not raise
