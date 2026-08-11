from pathlib import Path
from flask import Blueprint, render_template, send_from_directory, current_app, session
from app.auth import login_required

bp = Blueprint('main', __name__)


@bp.route('/')
@login_required
def index():
    authenticated = bool(session.get('authenticated'))
    return render_template('index.html', authenticated=authenticated)


@bp.route('/sw.js')
def service_worker():
    """Serve SW at root scope so it can control all pages under /."""
    response = current_app.send_static_file('sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


# Clips are immutable: the filename carries the Blink clip id and a downloaded
# file is never rewritten. Let the browser keep them so scrubbing and thumbnail
# redraws don't re-hit the origin. `private` keeps the CDN out of it — these are
# behind login.
VIDEO_MAX_AGE = 31536000  # 1 year


@bp.route('/videos/<path:filename>')
@login_required
def serve_video(filename):
    """Serve locally downloaded clips with HTTP range support.

    Werkzeug's conditional response implements Range/If-Range/416 correctly
    (including suffix ranges like `bytes=-500`, which the previous hand-rolled
    parser served as a *prefix*) and hands the file to the WSGI server's
    file_wrapper instead of a Python read loop — so a stalled player no longer
    pins a gunicorn thread for the whole download.
    """
    video_dir = (Path(current_app.root_path).parent / 'local_clips').resolve()

    # send_from_directory rejects traversal via safe_join and 404s on a miss.
    resp = send_from_directory(
        video_dir, filename, conditional=True, max_age=VIDEO_MAX_AGE
    )
    resp.headers['Accept-Ranges'] = 'bytes'
    # send_file marks the response `public` whenever max_age is set; these clips
    # sit behind login with Cloudflare in front, so force `private` instead.
    resp.cache_control.public = False
    resp.cache_control.private = True
    resp.cache_control.immutable = True
    return resp

