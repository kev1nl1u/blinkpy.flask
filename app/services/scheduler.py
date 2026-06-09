from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_JOB_ID = "daily_bulk_download"


class DownloadScheduler:
    """Daily bulk-download scheduler, reconfigurable at runtime."""

    def __init__(self, job):
        self._job = job
        self._scheduler = BackgroundScheduler()
        self._scheduler.start()

    def apply(self, cfg):
        """Create, update, or remove the job based on settings."""
        sd = cfg.get("scheduled_download", {})
        if self._scheduler.get_job(_JOB_ID):
            self._scheduler.remove_job(_JOB_ID)
        if not sd.get("enabled"):
            return
        hour, minute = sd["time"].split(":")
        trigger = CronTrigger(hour=int(hour), minute=int(minute), timezone=sd["timezone"])
        self._scheduler.add_job(self._job, trigger=trigger, id=_JOB_ID)

    def get_job(self):
        return self._scheduler.get_job(_JOB_ID)

    def shutdown(self):
        self._scheduler.shutdown(wait=False)
