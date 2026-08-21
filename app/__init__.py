import atexit
import os
from flask import Flask, session, request
from flask_session import Session
from pathlib import Path
from app.services.blink import service as blink_service, run_sync
from app.i18n import get_translation, get_all_translations, get_supported_languages
from app.auth import login_required
from blinkpy.auth import BlinkTwoFARequiredError


def create_app():
    app = Flask(__name__)

    from config import config
    config_name = os.environ.get('FLASK_ENV', 'development')
    app.config.from_object(config[config_name])

    Session(app)

    def get_current_language():
        # Priority: session → cookie → Accept-Language header → default
        if 'language' in session:
            lang = session['language']
            if lang in get_supported_languages():
                return lang
        lang = request.cookies.get('blink_language')
        if lang in get_supported_languages():
            return lang
        best = request.accept_languages.best_match(get_supported_languages())
        if best:
            return best
        return 'en'

    app.jinja_env.globals.update(
        t=lambda key: get_translation(key, get_current_language()),
        get_translation=get_translation,
        get_current_language=get_current_language,
        get_supported_languages=get_supported_languages,
    )

    app.extensions["blink_service"] = blink_service

    creds_path = Path("credentials.json")
    if creds_path.exists():
        try:
            run_sync(blink_service.start())
            app.logger.info("Blink service started from credentials.json")
        except BlinkTwoFARequiredError:
            app.logger.warning("Blink service requires 2FA; awaiting code.")
        except Exception as exc:
            app.logger.exception("Failed to start Blink service: %s", exc)

    from app.services.settings import Settings
    from app.services.scheduler import DownloadScheduler
    from app.services.blink.bulk import run_bulk_download
    from app.services.blink.motion import run_motion_poll
    from app.services.rearm import run_auto_rearm

    settings = Settings("settings.json")
    app.extensions["settings"] = settings
    scheduler = DownloadScheduler(
        job=lambda: run_bulk_download(blink_service),
        motion_job=lambda: run_motion_poll(blink_service),
        rearm_job=lambda: run_auto_rearm(blink_service, settings),
    )
    atexit.register(scheduler.shutdown)
    scheduler.apply(settings.load())
    app.extensions["scheduler"] = scheduler

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if not app.debug:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    from app.routes import bp as main_bp
    app.register_blueprint(main_bp)

    from app.auth_routes import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.api import bp as api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    @app.route('/api/language/<lang>', methods=['POST'])
    @login_required
    def set_language(lang):
        from flask import jsonify, make_response
        if lang not in get_supported_languages():
            return jsonify({'error': 'Unsupported language'}), 400
        session['language'] = lang
        response = make_response(jsonify({'success': True, 'language': lang}))
        response.set_cookie('blink_language', lang, max_age=31536000)
        return response

    return app
