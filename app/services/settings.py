import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULTS = {
    "scheduled_download": {
        "enabled": False,
        "time": "04:00",
        "timezone": "UTC",
    },
    "motion_download": {
        "enabled": False,
        "interval_minutes": 2,
    },
}


class Settings:
    """Load, validate, and persist app settings to a JSON file."""

    def __init__(self, path="settings.json"):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return _deep_copy(DEFAULTS)
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return _deep_copy(DEFAULTS)
        merged = _deep_copy(DEFAULTS)
        sd = data.get("scheduled_download", {})
        merged["scheduled_download"].update(
            {k: sd[k] for k in ("enabled", "time", "timezone") if k in sd}
        )
        md = data.get("motion_download", {})
        merged["motion_download"].update(
            {k: md[k] for k in ("enabled", "interval_minutes") if k in md}
        )
        return merged

    def validate(self, cfg):
        sd = cfg.get("scheduled_download")
        if sd is not None:
            if not isinstance(sd.get("enabled"), bool):
                raise ValueError("enabled must be a boolean")
            time = sd.get("time", "")
            if not _TIME_RE.match(str(time)):
                raise ValueError(f"invalid time: {time!r} (expected HH:MM)")
            tz = sd.get("timezone", "")
            try:
                ZoneInfo(str(tz))
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"invalid timezone: {tz!r}")

        md = cfg.get("motion_download")
        if md is not None:
            if not isinstance(md.get("enabled"), bool):
                raise ValueError("motion_download.enabled must be a boolean")
            interval = md.get("interval_minutes")
            if not isinstance(interval, int) or isinstance(interval, bool) \
                    or not (1 <= interval <= 60):
                raise ValueError(
                    f"invalid interval_minutes: {interval!r} (expected int 1-60)"
                )

    def save(self, cfg):
        self.validate(cfg)
        self.path.write_text(json.dumps(cfg, indent=2))


def _deep_copy(d):
    return json.loads(json.dumps(d))
