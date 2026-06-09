import pytest
from app.services.settings import Settings, DEFAULTS


def test_load_defaults_when_absent(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.load() == DEFAULTS


def test_save_load_roundtrip(tmp_path):
    s = Settings(tmp_path / "settings.json")
    cfg = {"scheduled_download": {"enabled": True, "time": "04:30", "timezone": "Europe/Rome"}}
    s.save(cfg)
    assert s.load() == cfg


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
