"""Auto re-arm: arm the Blink system once, at a scheduled moment.

The target instant lives in settings.json (``auto_rearm.at``) so a pending
re-arm survives a restart, and is mirrored into a one-shot APScheduler job by
:class:`~app.services.scheduler.DownloadScheduler`.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# One week: long enough for a holiday, short enough that a forgotten schedule
# cannot silently re-arm the house months later.
MAX_MINUTES = 7 * 24 * 60


def parse_at(value):
    """Parse an ISO-8601 instant into an aware datetime (naive means UTC).

    Browsers send ``…Z``; normalize it so parsing works before Python 3.11.
    """
    text = str(value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_run_at(minutes=None, at=None, now=None):
    """Resolve a re-arm request into an aware UTC datetime.

    Accepts either a relative delay (*minutes*) or an absolute ISO-8601
    instant (*at*, normally sent by the browser with its own UTC offset).
    Raises ValueError on anything out of range or already in the past.
    """
    now = now or datetime.now(timezone.utc)

    if minutes is not None:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise ValueError("minutes must be a number")
        minutes = int(minutes)
        if not (1 <= minutes <= MAX_MINUTES):
            raise ValueError(f"minutes out of range (1-{MAX_MINUTES})")
        return now + timedelta(minutes=minutes)

    if at is not None:
        try:
            run_at = parse_at(at)
        except (TypeError, ValueError):
            raise ValueError(f"invalid datetime: {at!r}")
        run_at = run_at.astimezone(timezone.utc)
        if run_at <= now:
            raise ValueError("datetime is in the past")
        if run_at > now + timedelta(minutes=MAX_MINUTES):
            raise ValueError(f"datetime too far ahead (max {MAX_MINUTES} minutes)")
        return run_at

    raise ValueError("either minutes or at is required")


def state(settings, now=None):
    """Return the pending re-arm as ``{at, seconds_remaining}`` (both None if off)."""
    at = (settings.load().get("auto_rearm") or {}).get("at")
    if not at:
        return {"at": None, "seconds_remaining": None}
    try:
        run_at = parse_at(at)
    except (TypeError, ValueError):
        return {"at": None, "seconds_remaining": None}
    now = now or datetime.now(timezone.utc)
    remaining = int((run_at - now).total_seconds())
    return {"at": at, "seconds_remaining": max(remaining, 0)}


def store(settings, scheduler, run_at):
    """Persist a pending re-arm (``run_at=None`` cancels) and re-sync the job."""
    cfg = settings.load()
    cfg["auto_rearm"] = {"at": run_at.isoformat() if run_at else None}
    settings.save(cfg)
    if scheduler:
        scheduler.apply(cfg)
    return cfg["auto_rearm"]


def clear(settings, scheduler):
    """Cancel any pending re-arm."""
    return store(settings, scheduler, None)


def run_auto_rearm(blink_service, settings):
    """Scheduler callback: arm every sync module, then clear the schedule.

    The stored instant is always cleared, even when arming fails — the job is
    one-shot, so leaving it behind would show a re-arm that can never fire.
    """
    armed = False
    try:
        if not blink_service or not blink_service.started or not blink_service.blink:
            logger.warning("auto re-arm skipped: Blink service not connected")
        else:
            for name, mod in (blink_service.blink.sync or {}).items():
                def _arm(mod=mod):
                    return mod.async_arm(True)
                blink_service.submit(0, _arm, timeout=180, label="arm")
                logger.info("auto re-arm: armed sync module %s", name)
            armed = True
    except Exception:
        logger.exception("auto re-arm failed")
    finally:
        try:
            cfg = settings.load()
            cfg["auto_rearm"] = {"at": None}
            settings.save(cfg)
        except Exception:
            logger.exception("auto re-arm: failed to clear schedule")
    return armed
