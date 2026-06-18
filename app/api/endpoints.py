import shutil
from datetime import datetime
from app.services.blink import run_sync
from app.services.blink.downloads import read_clip_meta
from pathlib import Path
from app.api import bp
from flask import jsonify, current_app, request
from blinkpy.auth import BlinkTwoFARequiredError
from app.auth import login_required

CREDENTIALS_FILE = Path("credentials.json")
DOWNLOAD_ROOT = Path("local_clips")


@bp.route('/status')
@login_required
def get_status():
    blink_service = current_app.extensions.get("blink_service")
    blink_ready   = blink_service.started      if blink_service else False
    awaiting_2fa  = blink_service.awaiting_2fa if blink_service else False

    armed = None
    if blink_ready and blink_service.blink and blink_service.blink.sync:
        # Fetch fresh network state from Blink API on every status call.
        # get_network_info() is lightweight (arm state only, no cameras).
        # Called only on page load / manual refresh — not on a poll loop.
        for mod in blink_service.blink.sync.values():
            try:
                def _net(mod=mod):
                    return mod.get_network_info()
                blink_service.submit(1, _net, timeout=60)
                v = mod.arm
                if v is not None:
                    armed = v
                    break
            except Exception:
                pass

    return jsonify({
        "status":          "API is online",
        "blink_connected": blink_ready,
        "awaiting_2fa":    awaiting_2fa,
        "armed":           armed,
    })


@bp.route('/blink/refresh', methods=['POST'])
@login_required
def blink_refresh():
    """Force-refresh Blink state (re-fetches network_info + camera states)."""
    blink_service = current_app.extensions.get("blink_service")
    if not blink_service or not blink_service.started:
        return jsonify({"error": "Blink service not connected"}), 400
    try:
        def _refresh():
            return blink_service.blink.refresh(force=True)
        blink_service.submit(1, _refresh, timeout=120)
        # Re-read arm state after refresh
        armed = None
        for mod in blink_service.blink.sync.values():
            v = getattr(mod, 'arm', None)
            if v is not None:
                armed = v
                break
        return jsonify({"ok": True, "armed": armed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/local-videos')
@login_required
def get_local_videos():
    """List locally downloaded clips. Reads sidecar JSON for real recording date."""
    def _sort_key(p: Path) -> float:
        meta = read_clip_meta(p)
        try:
            return datetime.fromisoformat(meta["created_at"]).timestamp()
        except Exception:
            return p.stat().st_mtime

    clips = []
    for mp4 in sorted(DOWNLOAD_ROOT.rglob('*.mp4'), key=_sort_key, reverse=True)[:50]:
        stem        = mp4.stem
        parts       = stem.split('_', 1)
        clip_id     = parts[0]
        camera_name = parts[1].replace('_', ' ') if len(parts) > 1 else stem
        meta        = read_clip_meta(mp4)
        # Prefer camera_name from sidecar (exact name from Blink), fall back to filename
        cam = meta.get("camera_name") or camera_name
        clips.append({
            "id":          clip_id,
            "camera_name": cam,
            "module":      mp4.parent.name,
            "filename":    mp4.name,
            "size":        mp4.stat().st_size,
            "created_at":  meta["created_at"],
            "url":         f"/videos/{mp4.parent.name}/{mp4.name}",
        })
    return jsonify({"videos": clips})


@bp.route('/credentials', methods=['POST'])
@login_required
def save_credentials():
    """Save Blink credentials and attempt to initialize service."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({"error": "email and password are required"}), 400
        
        # Try to initialize Blink service with new credentials
        blink_service = current_app.extensions.get("blink_service")
        if blink_service:
            try:
                run_sync(blink_service.login_with_credentials(email, password))
                return jsonify({
                    "message": "Credentials saved and Blink connected successfully",
                    "blink_connected": True,
                    "awaiting_2fa": False
                }), 200
            except BlinkTwoFARequiredError:
                return jsonify({
                    "message": "2FA required",
                    "awaiting_2fa": True,
                    "error": "Please provide 2FA code"
                }), 202
        
        return jsonify({
            "error": "Blink service not available"
        }), 500
    
    except Exception as e:
        return jsonify({"error": f"Failed to save credentials: {str(e)}"}), 500


@bp.route('/credentials/2fa', methods=['POST'])
@login_required
def submit_2fa():
    """Complete login with 2FA code."""
    try:
        data = request.get_json()
        
        if not data or 'code' not in data:
            return jsonify({"error": "2FA code is required"}), 400
        
        code = data.get('code')
        blink_service = current_app.extensions.get("blink_service")
        
        if not blink_service or not blink_service.awaiting_2fa:
            return jsonify({"error": "Session expired — re-enter Blink credentials", "session_expired": True}), 400
        
        try:
            run_sync(blink_service.send_2fa_code(code))
            return jsonify({
                "message": "2FA verified and Blink connected successfully",
                "blink_connected": True,
                "awaiting_2fa": False
            }), 200
        except Exception as e:
            return jsonify({
                "error": f"Invalid 2FA code: {str(e)}",
                "awaiting_2fa": True
            }), 401
    
    except Exception as e:
        return jsonify({"error": f"2FA verification failed: {str(e)}"}), 500


def _arm_modules(blink, value, module_name=None):
    """Arm or disarm sync module(s) via the priority queue (priority 0)."""
    blink_service = current_app.extensions.get("blink_service")
    if module_name:
        if module_name not in blink.sync:
            raise KeyError(f"Sync module '{module_name}' not found")
        modules = {module_name: blink.sync[module_name]}
    else:
        modules = blink.sync
    for name, mod in modules.items():
        def _arm(mod=mod):
            return mod.async_arm(value)
        blink_service.submit(0, _arm, timeout=180)
    return value


@bp.route('/blink/arm', methods=['POST'])
@login_required
def blink_arm():
    try:
        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400
        data        = request.get_json(silent=True) or {}
        module_name = data.get("module")
        armed       = _arm_modules(blink_service.blink, True, module_name)
        return jsonify({"ok": True, "armed": armed}), 200
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to arm: {str(e)}"}), 500


@bp.route('/blink/disarm', methods=['POST'])
@login_required
def blink_disarm():
    try:
        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400
        data        = request.get_json(silent=True) or {}
        module_name = data.get("module")
        armed       = _arm_modules(blink_service.blink, False, module_name)
        return jsonify({"ok": True, "armed": armed}), 200
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to disarm: {str(e)}"}), 500



@bp.route('/settings', methods=['GET'])
@login_required
def get_settings():
    settings = current_app.extensions.get("settings")
    return jsonify(settings.load()), 200


@bp.route('/settings', methods=['POST'])
@login_required
def post_settings():
    settings = current_app.extensions.get("settings")
    scheduler = current_app.extensions.get("scheduler")
    incoming = request.get_json(silent=True) or {}
    # Merge over current settings so clients can post a single section.
    cfg = settings.load()
    for section in ("scheduled_download", "motion_download"):
        if isinstance(incoming.get(section), dict):
            cfg[section].update(incoming[section])
    try:
        settings.save(cfg)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    scheduler.apply(cfg)
    return jsonify({"ok": True, "settings": cfg}), 200


@bp.route('/blink/local/clip/boost', methods=['POST'])
@login_required
def boost_clip():
    """Boost (or enqueue) a single remote clip to the front of the download queue."""
    blink_service = current_app.extensions.get("blink_service")
    if not blink_service or not blink_service.started:
        return jsonify({"error": "Blink service not connected"}), 400
    data = request.get_json(silent=True) or {}
    module_name = data.get("module")
    clip_id = data.get("clip_id")
    if not module_name or clip_id is None:
        return jsonify({"error": "module and clip_id are required"}), 400

    blink = blink_service.blink
    if module_name not in blink.sync:
        return jsonify({"error": f"Sync module '{module_name}' not found"}), 404

    mod = blink.sync[module_name]
    key = f"{module_name}:{clip_id}"

    manifest = getattr(mod, "_local_storage", {}).get("manifest", [])
    manifest_id = getattr(mod, "_local_storage", {}).get("last_manifest_id")
    item = next((i for i in manifest if str(i.id) == str(clip_id)), None)
    if item is None or not manifest_id:
        return jsonify({"error": "clip not in current manifest"}), 404

    from app.services.blink import downloads

    def _dl():
        return downloads.download_one_clip(blink, module_name, item, manifest_id)

    blink_service.reprioritize(key, 2)
    blink_service.submit_nowait(2, _dl, key=key)
    return jsonify({"ok": True, "key": key}), 200


@bp.route('/blink/local/remote', methods=['GET'])
@login_required
def get_remote_clips():
    """List clips present in the sync-module manifest (downloaded or not).

    Refreshes the manifest via the queue (priority 1) then returns metadata.
    Frontend merges these with local clips and marks the missing ones 'remote'.
    """
    blink_service = current_app.extensions.get("blink_service")
    if not blink_service or not blink_service.started:
        return jsonify({"error": "Blink service not connected"}), 400

    blink = blink_service.blink
    clips = []
    for mod_name, mod in blink.sync.items():
        try:
            def _refresh(mod=mod):
                return mod.update_local_storage_manifest()
            blink_service.submit(1, _refresh, timeout=120)
        except Exception:
            continue
        manifest = getattr(mod, "_local_storage", {}).get("manifest", [])
        for item in manifest:
            clips.append({
                "id": str(item.id),
                "module": mod_name,
                "camera_name": item.name,
                "created_at": item.created_at.isoformat()
                if hasattr(item.created_at, "isoformat") else str(item.created_at),
            })
    return jsonify({"clips": clips}), 200


@bp.route('/admin/reset', methods=['POST'])
@login_required
def admin_reset():
    """Stop Blink, delete credentials, all sessions, and all downloaded videos."""
    errors = []

    blink_service = current_app.extensions.get("blink_service")
    if blink_service:
        try:
            run_sync(blink_service.stop(), timeout=10)
        except Exception as e:
            errors.append(f"blink stop: {e}")

    try:
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
    except Exception as e:
        errors.append(f"credentials: {e}")

    try:
        if DOWNLOAD_ROOT.exists():
            shutil.rmtree(DOWNLOAD_ROOT)
    except Exception as e:
        errors.append(f"clips: {e}")

    # Wiping flask_session files invalidates all active sessions server-side
    try:
        sessions_dir = Path("flask_session")
        if sessions_dir.exists():
            shutil.rmtree(sessions_dir)
    except Exception as e:
        errors.append(f"sessions: {e}")

    if errors:
        return jsonify({"ok": False, "errors": errors}), 500
    return jsonify({"ok": True}), 200

