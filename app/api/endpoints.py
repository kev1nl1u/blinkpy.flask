import json
import os
import shutil
from datetime import datetime
from app.services.blink import run_sync
from pathlib import Path
from app.api import bp
from flask import jsonify, current_app, request, Response, stream_with_context
from blinkpy.auth import BlinkTwoFARequiredError
from blinkpy import api as blink_api
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
        meta = _read_clip_meta(p)
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
        meta        = _read_clip_meta(mp4)
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


@bp.route('/blink/local/manifest', methods=['POST'])
@login_required
def request_local_manifest():
    """Request an updated local storage manifest for a sync module (or all)."""
    try:
        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400

        data = request.get_json(silent=True) or {}
        module_name = data.get("module")

        blink = blink_service.blink
        results = {}

        if module_name:
            if module_name not in blink.sync:
                return jsonify({"error": f"Sync module '{module_name}' not found"}), 404
            mod = blink.sync[module_name]
            resp = run_sync(blink_api.request_local_storage_manifest(blink, mod.network_id, mod.sync_id))
            results[module_name] = resp
            return jsonify({"results": results}), 200

        # All modules
        for name, mod in blink.sync.items():
            try:
                resp = run_sync(blink_api.request_local_storage_manifest(blink, mod.network_id, mod.sync_id))
                results[name] = resp
            except Exception as e:
                results[name] = {"error": str(e)}

        return jsonify({"results": results}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to request local manifest: {str(e)}"}), 500


@bp.route('/blink/local/manifest', methods=['GET'])
@login_required
def get_local_manifest():
    """Get a previously requested manifest by request id. Query params: module, request_id"""
    try:
        module_name = request.args.get('module')
        request_id = request.args.get('request_id')

        if not module_name or not request_id:
            return jsonify({"error": "module and request_id query params are required"}), 400

        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400

        blink = blink_service.blink
        if module_name not in blink.sync:
            return jsonify({"error": f"Sync module '{module_name}' not found"}), 404

        mod = blink.sync[module_name]
        resp = run_sync(blink_api.get_local_storage_manifest(blink, mod.network_id, mod.sync_id, request_id))
        return jsonify({"manifest": resp}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to get local manifest: {str(e)}"}), 500


@bp.route('/blink/local/clip', methods=['POST'])
@login_required
def request_local_clip():
    """Prepare a local storage clip for download. Body: module, manifest_id, clip_id"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        module_name = data.get('module')
        manifest_id = data.get('manifest_id')
        clip_id = data.get('clip_id')

        if not module_name or not manifest_id or not clip_id:
            return jsonify({"error": "module, manifest_id and clip_id are required"}), 400

        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400

        blink = blink_service.blink
        if module_name not in blink.sync:
            return jsonify({"error": f"Sync module '{module_name}' not found"}), 404

        mod = blink.sync[module_name]
        resp = run_sync(blink_api.request_local_storage_clip(blink, mod.network_id, mod.sync_id, manifest_id, clip_id))
        return jsonify({"result": resp}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to prepare local clip: {str(e)}"}), 500


@bp.route('/blink/local/clips', methods=['GET'])
@login_required
def get_all_local_clips():
    """Fetch and list all local storage clips with download URLs. Optional: module=<name>"""
    try:
        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400

        module_name = request.args.get('module')
        blink = blink_service.blink

        all_clips = {}

        # Determine which modules to process
        if module_name:
            if module_name not in blink.sync:
                return jsonify({"error": f"Sync module '{module_name}' not found"}), 404
            modules = {module_name: blink.sync[module_name]}
        else:
            modules = blink.sync

        # For each module, update manifest and collect clips
        for name, mod in modules.items():
            last_manifest_id = None
            try:
                # Update local storage manifest (this polls and loads clips)
                run_sync(mod.update_local_storage_manifest())
                
                clips_list = []
                
                # Get clips from the now-updated local_storage manifest
                if hasattr(mod, '_local_storage') and mod._local_storage.get('manifest'):
                    manifest = mod._local_storage['manifest']
                    last_manifest_id = mod._local_storage.get('last_manifest_id')
                    
                    for item in manifest:
                        try:
                            clip_data = {
                                "id": item.id,
                                "name": item.name,
                                "created_at": item.created_at.isoformat() if hasattr(item.created_at, 'isoformat') else str(item.created_at),
                                "size": item.size,
                            }
                            # Get download URL
                            if last_manifest_id:
                                clip_data["url"] = item.url(last_manifest_id)
                            
                            clips_list.append(clip_data)
                        except Exception as e:
                            clips_list.append({"error": str(e)})

                all_clips[name] = {
                    "clips": clips_list,
                    "count": len(clips_list),
                    "manifest_id": last_manifest_id
                }
            except Exception as e:
                all_clips[name] = {"error": str(e), "count": 0, "clips": []}

        return jsonify({
            "clips_by_module": all_clips,
            "total_clips": sum(c.get('count', 0) for c in all_clips.values() if 'count' in c)
        }), 200

    except Exception as e:
        return jsonify({"error": f"Failed to fetch local clips: {str(e)}"}), 500


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name).strip().replace(' ', '_')


def _find_existing_clip(dest: Path, clip_id) -> Path | None:
    """Return the first existing .mp4 whose stem starts with '{clip_id}_', or None."""
    if not dest.exists():
        return None
    prefix = f"{clip_id}_"
    for p in dest.glob("*.mp4"):
        if p.stem.startswith(prefix):
            return p
    return None


def _write_clip_meta(path: Path, item) -> None:
    """Write sidecar JSON with real recording date and set file mtime to match."""
    try:
        created_at: datetime = item.created_at
        ts = created_at.timestamp()
        # Set file modification time to actual recording time
        os.utime(path, (ts, ts))
        # Sidecar JSON so mtime survives copies / backups
        meta_path = path.with_suffix('.json')
        meta_path.write_text(json.dumps({
            "created_at": created_at.isoformat(),
            "camera_name": item.name,
            "id": item.id,
        }))
    except Exception:
        pass  # metadata loss is non-fatal


def _read_clip_meta(mp4_path: Path) -> dict:
    """Read sidecar JSON if present, fall back to mtime."""
    meta_path = mp4_path.with_suffix('.json')
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            pass
    # Fallback: file mtime (may be download time, not recording time)
    return {"created_at": datetime.fromtimestamp(mp4_path.stat().st_mtime).isoformat()}


@bp.route('/blink/local/download/stream')
@login_required
def download_clips_stream():
    """SSE endpoint: download new clips and stream progress events to the frontend."""
    blink_service = current_app.extensions.get("blink_service")
    if not blink_service or not blink_service.started:
        return jsonify({"error": "Blink service not connected"}), 400

    blink      = blink_service.blink
    target_dir = DOWNLOAD_ROOT

    def _emit(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def generate():
        try:
            for mod_name, mod in blink.sync.items():
                # Refresh manifest from sync module
                try:
                    run_sync(mod.update_local_storage_manifest())
                except Exception as exc:
                    yield _emit({"type": "error", "message": str(exc)})
                    return

                last_manifest_id = mod._local_storage.get('last_manifest_id')
                if not last_manifest_id:
                    yield _emit({"type": "done", "downloaded": 0, "skipped": 0, "errors": 0})
                    return

                manifest = mod._local_storage.get('manifest', [])
                all_items = sorted(manifest, key=lambda x: x.created_at, reverse=True)[:20]

                # Split into existing (skip + fix meta) and new (download)
                new_items, skipped = [], 0
                for item in all_items:
                    safe_name  = _safe_filename(item.name or str(item.id))
                    dest       = target_dir / mod_name
                    final_path = dest / f"{item.id}_{safe_name}.mp4"
                    existing = _find_existing_clip(dest, item.id)
                    if existing:
                        skipped += 1
                        if not existing.with_suffix('.json').exists():
                            _write_clip_meta(existing, item)
                    else:
                        new_items.append((item, dest, final_path))

                total = len(new_items)
                yield _emit({"type": "start", "total": total, "skipped": skipped})

                downloaded, errors = 0, 0
                for idx, (item, dest, final_path) in enumerate(new_items):
                    yield _emit({
                        "type":    "progress",
                        "done":    idx,
                        "total":   total,
                        "current": item.name,
                        "id":      item.id,
                    })
                    try:
                        dest.mkdir(parents=True, exist_ok=True)
                        item.url(last_manifest_id)
                        run_sync(item.prepare_download(blink), timeout=120)
                        ok = run_sync(item.download_video(blink, str(final_path)), timeout=180)
                        if ok:
                            downloaded += 1
                            _write_clip_meta(final_path, item)
                            # Emit full clip metadata so frontend can add it immediately
                            yield _emit({
                                "type":        "clip",
                                "id":          str(item.id),
                                "camera_name": item.name,
                                "created_at":  item.created_at.isoformat(),
                                "url":         f"/videos/{mod_name}/{final_path.name}",
                                "size":        final_path.stat().st_size,
                            })
                        else:
                            errors += 1
                    except Exception as exc:
                        errors += 1
                        if final_path.exists() and final_path.stat().st_size == 0:
                            final_path.unlink(missing_ok=True)

                yield _emit({"type": "done", "downloaded": downloaded, "skipped": skipped, "errors": errors})

        except Exception as exc:
            yield _emit({"type": "error", "message": str(exc)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@bp.route('/blink/local/download', methods=['POST'])
@login_required
def download_local_clips():
    """Download local storage clips to disk. Body: optional `module`, optional `target_dir`.
    Downloads all clips for module(s) unless `module` omitted (then all modules).
    Skips files that already exist.
    """
    try:
        data = request.get_json(silent=True) or {}
        module_name = data.get('module')
        # Use fixed download directory
        target_dir = DOWNLOAD_ROOT

        blink_service = current_app.extensions.get("blink_service")
        if not blink_service or not blink_service.started:
            return jsonify({"error": "Blink service not connected"}), 400

        blink = blink_service.blink

        # Choose modules
        if module_name:
            if module_name not in blink.sync:
                return jsonify({"error": f"Sync module '{module_name}' not found"}), 404
            modules = {module_name: blink.sync[module_name]}
        else:
            modules = blink.sync

        summary = {}

        PREPARE_TIMEOUT = 120   # seconds to wait for sync module → Blink cloud upload
        DOWNLOAD_TIMEOUT = 180  # seconds to wait for Blink cloud → our server download

        for name, mod in modules.items():
            try:
                run_sync(mod.update_local_storage_manifest())
                clips_downloaded = 0
                clips_skipped = 0
                clip_errors = []  # list of (clip_id, error_str) for visibility

                last_manifest_id = mod._local_storage.get('last_manifest_id') if hasattr(mod, '_local_storage') else None
                if not last_manifest_id:
                    summary[name] = {'error': 'No manifest ID — local storage may be unavailable'}
                    continue

                manifest = mod._local_storage.get('manifest', [])
                # Process newest-first; limit to 20 per request to avoid HTTP timeout
                items = sorted(manifest, key=lambda x: x.created_at, reverse=True)[:20]

                for item in items:
                    clip_id = item.id
                    try:
                        safe_name = _safe_filename(item.name or str(clip_id))
                        filename = f"{clip_id}_{safe_name}.mp4"
                        dest = Path(target_dir) / name
                        dest.mkdir(parents=True, exist_ok=True)
                        final_path = dest / filename

                        existing = _find_existing_clip(dest, clip_id)
                        if existing:
                            clips_skipped += 1
                            if not existing.with_suffix('.json').exists():
                                _write_clip_meta(existing, item)
                            continue

                        item.url(last_manifest_id)
                        run_sync(item.prepare_download(blink), timeout=PREPARE_TIMEOUT)
                        downloaded = run_sync(item.download_video(blink, str(final_path)), timeout=DOWNLOAD_TIMEOUT)
                        if downloaded:
                            clips_downloaded += 1
                            _write_clip_meta(final_path, item)
                        else:
                            clip_errors.append((clip_id, 'download_video returned False'))
                    except Exception as exc:
                        clip_errors.append((clip_id, str(exc)))
                        # Clean up partial file
                        if 'final_path' in dir() and final_path.exists() and final_path.stat().st_size == 0:
                            final_path.unlink(missing_ok=True)

                summary[name] = {
                    'downloaded': clips_downloaded,
                    'skipped': clips_skipped,
                    'errors': len(clip_errors),
                    'error_details': clip_errors[:5],  # first 5 errors surfaced
                }
            except Exception as e:
                summary[name] = {'error': str(e)}

        return jsonify({"summary": summary}), 200

    except Exception as e:
        return jsonify({"error": f"Failed to download clips: {str(e)}"}), 500


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

