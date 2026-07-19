import json
import os
from pathlib import Path
from datetime import datetime

DOWNLOAD_ROOT = Path("local_clips")


def safe_filename(name: str) -> str:
    return "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_" for c in name
    ).strip().replace(" ", "_")


def find_existing_clip(dest: Path, clip_id) -> Path | None:
    if not dest.exists():
        return None
    prefix = f"{clip_id}_"
    for p in dest.glob("*.mp4"):
        if p.stem.startswith(prefix):
            return p
    return None


def write_clip_meta(path: Path, item) -> None:
    try:
        created_at: datetime = item.created_at
        ts = created_at.timestamp()
        os.utime(path, (ts, ts))
        path.with_suffix(".json").write_text(json.dumps({
            "created_at": created_at.isoformat(),
            "camera_name": item.name,
            "id": item.id,
        }))
    except Exception:
        pass  # metadata loss is non-fatal


def read_clip_meta(mp4_path: Path) -> dict:
    meta_path = mp4_path.with_suffix(".json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    return {"created_at": datetime.fromtimestamp(mp4_path.stat().st_mtime).isoformat()}


async def download_one_clip(blink, mod_name, item, manifest_id, root=DOWNLOAD_ROOT,
                            prepare_timeout=120, download_timeout=180):
    """Download a single clip. Returns a dict with status and metadata.

    status: "skipped" | "downloaded" | "error"
    """
    dest = Path(root) / mod_name
    existing = find_existing_clip(dest, item.id)
    if existing:
        if not existing.with_suffix(".json").exists():
            write_clip_meta(existing, item)
        return {"status": "skipped", "id": str(item.id)}

    dest.mkdir(parents=True, exist_ok=True)
    final_path = dest / f"{item.id}_{safe_filename(item.name or str(item.id))}.mp4"
    try:
        item.url(manifest_id)
        await item.prepare_download(blink)
        ok = await item.download_video(blink, str(final_path))
        if not ok:
            _cleanup_partial(final_path)
            return {"status": "error", "id": str(item.id), "error": "download_video returned False"}
        write_clip_meta(final_path, item)
        return {
            "status": "downloaded",
            "id": str(item.id),
            "camera_name": item.name,
            "created_at": item.created_at.isoformat(),
            "url": f"/videos/{mod_name}/{final_path.name}",
            "size": final_path.stat().st_size,
        }
    except Exception as exc:
        _cleanup_partial(final_path)
        return {"status": "error", "id": str(item.id), "error": str(exc)}


def _cleanup_partial(path: Path):
    if path.exists():
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
