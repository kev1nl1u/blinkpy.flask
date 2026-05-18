from pathlib import Path
from flask import Blueprint, render_template, send_from_directory, current_app, session, request, Response, abort
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


@bp.route('/videos/<path:filename>')
@login_required
def serve_video(filename):
    """Stream locally downloaded clips with proper HTTP range support."""
    video_dir = Path(current_app.root_path).parent / 'local_clips'
    file_path = (video_dir / filename).resolve()

    # Security: ensure resolved path is inside video_dir
    if not str(file_path).startswith(str(video_dir.resolve())):
        abort(403)
    if not file_path.exists() or not file_path.is_file():
        abort(404)

    file_size = file_path.stat().st_size
    range_header = request.headers.get('Range')

    if range_header:
        try:
            unit, ranges_str = range_header.strip().split('=', 1)
            if unit != 'bytes':
                abort(416)
            range_parts = ranges_str.split('-', 1)
            start = int(range_parts[0]) if range_parts[0] else 0
            end   = int(range_parts[1]) if len(range_parts) > 1 and range_parts[1] else file_size - 1
        except (ValueError, IndexError):
            abort(416)

        if start >= file_size or end >= file_size or start > end:
            resp = Response(status=416)
            resp.headers['Content-Range'] = f'bytes */{file_size}'
            return resp

        length = end - start + 1

        def _stream():
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return Response(
            _stream(), 206,
            mimetype='video/mp4',
            headers={
                'Content-Range':  f'bytes {start}-{end}/{file_size}',
                'Accept-Ranges':  'bytes',
                'Content-Length': str(length),
                'Cache-Control':  'no-store',
            },
        )

    # Full file — still advertise range support so player can seek
    resp = send_from_directory(video_dir, filename, conditional=True)
    resp.headers['Accept-Ranges'] = 'bytes'
    return resp

