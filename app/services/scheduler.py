import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_JOB_ID = "daily_bulk_download"
_MOTION_JOB_ID = "motion_poll_download"
_REARM_JOB_ID = "auto_rearm"


class DownloadScheduler:
    """Download scheduler, reconfigurable at runtime.

    Runs three independent jobs:
      - a daily cron bulk download (`scheduled_download`);
      - an interval motion poll (`motion_download`) that downloads new clips
        from armed sync modules near-real-time;
      - a one-shot auto re-arm (`auto_rearm`) that arms the system again at a
        chosen instant after the user disarmed it.
    """

    def __init__(self, job, motion_job=None, rearm_job=None):
        self._job = job
        self._motion_job = motion_job
        self._rearm_job = rearm_job
        self._scheduler = BackgroundScheduler()
        self._scheduler.start()

    def apply(self, cfg):
        """Create, update, or remove every job based on settings."""
        self._apply_cron(cfg.get("scheduled_download", {}))
        self._apply_motion(cfg.get("motion_download", {}))
        self._apply_rearm(cfg.get("auto_rearm", {}))

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

    def _apply_rearm(self, ar):
        """(Re)schedule the one-shot re-arm from its stored ISO-8601 instant."""
        if self._scheduler.get_job(_REARM_JOB_ID):
            self._scheduler.remove_job(_REARM_JOB_ID)
        at = (ar or {}).get("at")
        if not at or self._rearm_job is None:
            return
        try:
            run_date = datetime.fromisoformat(str(at))
        except (TypeError, ValueError):
            logger.warning("auto_rearm: ignoring unparseable instant %r", at)
            return
        if run_date.tzinfo is None:
            run_date = run_date.replace(tzinfo=timezone.utc)
        # A missed instant (server was down) still deserves the re-arm, so run
        # it shortly instead of dropping it on the floor.
        now = datetime.now(timezone.utc)
        if run_date <= now:
            run_date = now + timedelta(seconds=5)
        self._scheduler.add_job(
            self._rearm_job, trigger=DateTrigger(run_date=run_date),
            id=_REARM_JOB_ID, misfire_grace_time=None,
        )

    def get_job(self):
        return self._scheduler.get_job(_JOB_ID)

    def get_motion_job(self):
        return self._scheduler.get_job(_MOTION_JOB_ID)

    def get_rearm_job(self):
        return self._scheduler.get_job(_REARM_JOB_ID)

    def jobs_info(self):
        """Return next-run info for every scheduled job (for introspection)."""
        def _next(job):
            nrt = getattr(job, "next_run_time", None) if job else None
            return nrt.isoformat() if nrt else None
        bulk = self._scheduler.get_job(_JOB_ID)
        motion = self._scheduler.get_job(_MOTION_JOB_ID)
        rearm = self._scheduler.get_job(_REARM_JOB_ID)
        return {
            "bulk":   {"enabled": bulk is not None,   "next_run": _next(bulk)},
            "motion": {"enabled": motion is not None, "next_run": _next(motion)},
            "rearm":  {"enabled": rearm is not None,  "next_run": _next(rearm)},
        }

    def shutdown(self):
        self._scheduler.shutdown(wait=False)
