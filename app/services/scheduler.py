from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_JOB_ID = "daily_bulk_download"
_MOTION_JOB_ID = "motion_poll_download"


class DownloadScheduler:
    """Download scheduler, reconfigurable at runtime.

    Runs two independent jobs:
      - a daily cron bulk download (`scheduled_download`);
      - an interval motion poll (`motion_download`) that downloads new clips
        from armed sync modules near-real-time.
    """

    def __init__(self, job, motion_job=None):
        self._job = job
        self._motion_job = motion_job
        self._scheduler = BackgroundScheduler()
        self._scheduler.start()

    def apply(self, cfg):
        """Create, update, or remove both jobs based on settings."""
        self._apply_cron(cfg.get("scheduled_download", {}))
        self._apply_motion(cfg.get("motion_download", {}))

    def _apply_cron(self, sd):
        if self._scheduler.get_job(_JOB_ID):
            self._scheduler.remove_job(_JOB_ID)
        if not sd.get("enabled"):
            return
        hour, minute = sd["time"].split(":")
        trigger = CronTrigger(hour=int(hour), minute=int(minute), timezone=sd["timezone"])
        self._scheduler.add_job(self._job, trigger=trigger, id=_JOB_ID)

    def _apply_motion(self, md):
        if self._scheduler.get_job(_MOTION_JOB_ID):
            self._scheduler.remove_job(_MOTION_JOB_ID)
        if not md.get("enabled") or self._motion_job is None:
            return
        trigger = IntervalTrigger(minutes=int(md.get("interval_minutes", 2)))
        self._scheduler.add_job(self._motion_job, trigger=trigger, id=_MOTION_JOB_ID)

    def get_job(self):
        return self._scheduler.get_job(_JOB_ID)

    def get_motion_job(self):
        return self._scheduler.get_job(_MOTION_JOB_ID)

    def jobs_info(self):
        """Return next-run info for both scheduled jobs (for introspection)."""
        def _next(job):
            nrt = getattr(job, "next_run_time", None) if job else None
            return nrt.isoformat() if nrt else None
        bulk = self._scheduler.get_job(_JOB_ID)
        motion = self._scheduler.get_job(_MOTION_JOB_ID)
        return {
            "bulk":   {"enabled": bulk is not None,   "next_run": _next(bulk)},
            "motion": {"enabled": motion is not None, "next_run": _next(motion)},
        }

    def shutdown(self):
        self._scheduler.shutdown(wait=False)
