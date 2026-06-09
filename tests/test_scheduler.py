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
