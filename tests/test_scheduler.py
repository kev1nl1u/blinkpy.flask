from app.services.scheduler import DownloadScheduler


def test_disabled_means_no_job():
    sched = DownloadScheduler(job=lambda: None)
    sched.apply({"scheduled_download": {"enabled": False, "time": "04:00", "timezone": "UTC"}})
    assert sched.get_job() is None
    sched.shutdown()


def test_enabled_creates_job_with_trigger():
    sched = DownloadScheduler(job=lambda: None)
    sched.apply({"scheduled_download": {"enabled": True, "time": "04:30", "timezone": "UTC"}})
    job = sched.get_job()
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "4"
    assert fields["minute"] == "30"
    sched.shutdown()


def test_reschedule_replaces_job():
    sched = DownloadScheduler(job=lambda: None)
    sched.apply({"scheduled_download": {"enabled": True, "time": "04:00", "timezone": "UTC"}})
    sched.apply({"scheduled_download": {"enabled": True, "time": "05:00", "timezone": "UTC"}})
    job = sched.get_job()
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "5"
    sched.shutdown()


def test_motion_disabled_means_no_motion_job():
    sched = DownloadScheduler(job=lambda: None, motion_job=lambda: None)
    sched.apply({"motion_download": {"enabled": False, "interval_minutes": 2}})
    assert sched.get_motion_job() is None
    sched.shutdown()


def test_motion_enabled_creates_interval_job():
    sched = DownloadScheduler(job=lambda: None, motion_job=lambda: None)
    sched.apply({"motion_download": {"enabled": True, "interval_minutes": 3}})
    job = sched.get_motion_job()
    assert job is not None
    assert int(job.trigger.interval.total_seconds()) == 180
    sched.shutdown()


def test_motion_reschedule_changes_interval():
    sched = DownloadScheduler(job=lambda: None, motion_job=lambda: None)
    sched.apply({"motion_download": {"enabled": True, "interval_minutes": 2}})
    sched.apply({"motion_download": {"enabled": True, "interval_minutes": 5}})
    job = sched.get_motion_job()
    assert int(job.trigger.interval.total_seconds()) == 300
    sched.shutdown()
