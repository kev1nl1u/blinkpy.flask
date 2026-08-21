import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import rearm
from app.services.scheduler import DownloadScheduler
from app.services.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "settings.json")


def test_compute_run_at_minutes():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert rearm.compute_run_at(minutes=30, now=now) == now + timedelta(minutes=30)


@pytest.mark.parametrize("minutes", [0, -5, rearm.MAX_MINUTES + 1, "30", True])
def test_compute_run_at_rejects_bad_minutes(minutes):
    with pytest.raises(ValueError):
        rearm.compute_run_at(minutes=minutes)


def test_compute_run_at_parses_browser_iso():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    run_at = rearm.compute_run_at(at="2026-01-01T15:30:00.000Z", now=now)
    assert run_at == datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)


def test_compute_run_at_keeps_client_offset():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    run_at = rearm.compute_run_at(at="2026-01-01T15:30:00+02:00", now=now)
    assert run_at == datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc)


def test_compute_run_at_rejects_past_and_far_future():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        rearm.compute_run_at(at="2026-01-01T11:59:00Z", now=now)
    with pytest.raises(ValueError):
        rearm.compute_run_at(at="2027-01-01T12:00:00Z", now=now)


def test_compute_run_at_requires_an_argument():
    with pytest.raises(ValueError):
        rearm.compute_run_at()


def test_store_and_clear_roundtrip(settings):
    run_at = datetime.now(timezone.utc) + timedelta(minutes=45)
    rearm.store(settings, None, run_at)
    state = rearm.state(settings)
    assert state["at"] == run_at.isoformat()
    assert 2600 <= state["seconds_remaining"] <= 2700

    rearm.clear(settings, None)
    assert rearm.state(settings) == {"at": None, "seconds_remaining": None}


def test_state_never_reports_negative_remaining(settings):
    rearm.store(settings, None, datetime.now(timezone.utc) + timedelta(minutes=1))
    now = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert rearm.state(settings, now=now)["seconds_remaining"] == 0


def test_state_ignores_corrupt_instant(settings):
    settings.path.write_text(json.dumps({"auto_rearm": {"at": "not-a-date"}}))
    assert rearm.state(settings) == {"at": None, "seconds_remaining": None}


def test_run_auto_rearm_arms_and_clears_schedule(settings):
    calls = []

    class FakeMod:
        async def async_arm(self, value): return value

    class FakeService:
        started = True
        blink = type("B", (), {"sync": {"mod": FakeMod()}})()
        def submit(self, priority, factory, key=None, timeout=None, label=None):
            calls.append((priority, label))
            return True

    rearm.store(settings, None, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert rearm.run_auto_rearm(FakeService(), settings) is True
    assert calls == [(0, "arm")]
    assert rearm.state(settings)["at"] is None


def test_run_auto_rearm_clears_schedule_when_disconnected(settings):
    class FakeService:
        started = False
        blink = None

    rearm.store(settings, None, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert rearm.run_auto_rearm(FakeService(), settings) is False
    assert rearm.state(settings)["at"] is None


def test_scheduler_creates_one_shot_job():
    sched = DownloadScheduler(job=lambda: None, rearm_job=lambda: None)
    run_at = datetime.now(timezone.utc) + timedelta(minutes=20)
    sched.apply({"auto_rearm": {"at": run_at.isoformat()}})
    job = sched.get_rearm_job()
    assert job is not None
    assert job.trigger.run_date == run_at
    assert sched.jobs_info()["rearm"]["enabled"] is True
    sched.shutdown()


def test_scheduler_removes_job_when_cleared():
    sched = DownloadScheduler(job=lambda: None, rearm_job=lambda: None)
    sched.apply({"auto_rearm": {"at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()}})
    sched.apply({"auto_rearm": {"at": None}})
    assert sched.get_rearm_job() is None
    sched.shutdown()


def test_scheduler_reschedules_missed_instant_instead_of_dropping_it():
    """Server downtime must not swallow a re-arm that came due meanwhile."""
    sched = DownloadScheduler(job=lambda: None, rearm_job=lambda: None)
    past = datetime.now(timezone.utc) - timedelta(hours=3)
    sched.apply({"auto_rearm": {"at": past.isoformat()}})
    job = sched.get_rearm_job()
    assert job is not None
    assert job.trigger.run_date > datetime.now(timezone.utc)
    sched.shutdown()


def test_settings_rejects_invalid_instant(settings):
    with pytest.raises(ValueError):
        settings.save({"auto_rearm": {"at": "tomorrow-ish"}})
