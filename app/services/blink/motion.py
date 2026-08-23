"""Motion-driven downloads for local-storage clips.

Blink has no push/webhook for new recordings, and the cheap `homescreen`
endpoint does NOT reflect new local-storage clips (verified: only config/arm
changes bump it). The only reliable signal is the local-storage manifest, whose
build wakes the sync module. To bound that cost we only build it when a module
is *armed* — the sole state in which motion can produce a recording.

`run_motion_poll` is fired on an interval by the scheduler. Per sync module:
  1. cheap armed check (1 request) — disarmed modules are skipped;
  2. if armed, build the manifest and enqueue only clips newer than the last
     poll, at boosted priority (2).
"""

from app.services.blink import downloads
from app.services.blink.manifest import prune_manifest

# In-memory high-water mark: newest clip `created_at` already seen, per module.
# Reset on process restart; the first poll after a restart seeds it without
# dumping history (that backlog is the scheduled bulk download's job).
_last_seen = {}


def run_motion_poll(service):
    """Scheduler entry point: enqueue downloads for new clips on armed modules."""
    if not service.started:
        try:
            service.start_from_credentials()
        except Exception:
            return  # 2FA required or no creds — skip this run
        if not service.started:
            return

    blink = service.blink
    for mod_name, mod in blink.sync.items():
        # Cheap armed check (one network-status request) before any manifest build.
        def _net(mod=mod):
            return mod.get_network_info()
        try:
            service.submit(1, _net, timeout=60, label="status")
        except Exception:
            continue
        if not mod.arm:
            continue  # disarmed — no recordings possible, skip the expensive build

        def _refresh(mod=mod):
            return mod.update_local_storage_manifest()
        try:
            service.submit(1, _refresh, timeout=300, label="manifest")
        except Exception:
            continue

        manifest = prune_manifest(mod)
        manifest_id = mod._local_storage.get("last_manifest_id")
        if not manifest_id or not manifest:
            continue

        newest = max((it.created_at for it in manifest), default=None)
        last = _last_seen.get(mod_name)
        if last is None:
            # First poll for this module: seed the mark, don't re-download history.
            _last_seen[mod_name] = newest
            continue

        for item in manifest:
            if item.created_at <= last:
                continue
            key = f"{mod_name}:{item.id}"

            def _dl(mod_name=mod_name, item=item, manifest_id=manifest_id):
                return downloads.download_one_clip(blink, mod_name, item, manifest_id)

            service.submit_nowait(2, _dl, key=key, label="download")

        if newest is not None:
            _last_seen[mod_name] = newest
