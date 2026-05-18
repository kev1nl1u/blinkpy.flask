import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        import secrets as _secrets
        SECRET_KEY = _secrets.token_hex(32)
        import warnings
        warnings.warn(
            'SECRET_KEY not set: sessions will not persist across restarts. '
            'Set SECRET_KEY in .env for production.',
            stacklevel=2,
        )

    SESSION_TYPE = 'filesystem'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_COOKIE_HTTPONLY = True
    # Secure=True in production; disabled automatically when FLASK_DEBUG is set
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_DEBUG', 'false').lower() not in ('1', 'true', 'yes')
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_NAME = 'blink_session'
    SESSION_REFRESH_EACH_REQUEST = True


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
