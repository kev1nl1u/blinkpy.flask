from app.services.blink import downloads
from app.services.blink.manifest import prune_manifest


def run_bulk_download(service):
    """Scheduler entry point: enqueue a manifest refresh and per-clip downloads.

    Attempts to start the service from saved credentials if not connected.
    """
    if not service.started:
        try:
            service.start_from_credentials()
        except Exception:
            return  # 2FA required or no creds — skip this run
        if not service.started:
            return

    blink = service.blink
    for mod_name, mod in blink.sync.items():
        def _refresh(mod=mod):
            return mod.update_local_storage_manifest()
        service.submit(1, _refresh, timeout=300, label="manifest")

        manifest = prune_manifest(mod)
        manifest_id = mod._local_storage.get("last_manifest_id")
        if not manifest_id:
            continue
        for item in manifest:
            key = f"{mod_name}:{item.id}"

            def _dl(mod_name=mod_name, item=item, manifest_id=manifest_id):
                return downloads.download_one_clip(blink, mod_name, item, manifest_id)

            service.submit_nowait(3, _dl, key=key, label="download")
