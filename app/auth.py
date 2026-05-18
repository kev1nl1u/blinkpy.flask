"""Google OAuth 2.0 Authorization Code Flow — auth manager and login_required decorator."""

import os
import secrets
import requests
from urllib.parse import urlencode
from functools import wraps

from flask import session, request, current_app, redirect, url_for
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


class GoogleOAuth2Manager:
    """Handles the full Google OAuth 2.0 Authorization Code Flow."""

    def __init__(self):
        self.client_id = os.environ.get('GOOGLE_CLIENT_ID')
        self.client_secret = os.environ.get('GOOGLE_CLIENT_SECRET')
        self.allowed_email = os.environ.get('ALLOWED_EMAIL')
        self.redirect_uri = os.environ.get('OAUTH2_REDIRECT_URI', 'http://localhost:5000/oauth2callback')

    def genera_url_login(self):
        """Build Google authorization URL and return (url, state_token)."""
        state = secrets.token_urlsafe(32)
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'consent',  # always show account picker so the user isn't silently logged in
        }
        auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)
        return auth_url, state

    def verifica_state(self, state_ricevuto):
        """Raise ValueError if the received state token doesn't match the session value (CSRF check)."""
        state_sessione = session.get('oauth2_state')
        if not state_ricevuto or not state_sessione or state_ricevuto != state_sessione:
            raise ValueError('Invalid or missing state token (possible CSRF attack)')

    def scambia_codice_con_token(self, codice):
        """Exchange the authorization code for tokens via Google's token endpoint."""
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': codice,
            'grant_type': 'authorization_code',
            'redirect_uri': self.redirect_uri,
        }
        risposta = requests.post('https://oauth2.googleapis.com/token', data=data, timeout=10)
        risposta.raise_for_status()
        return risposta.json()

    def decodifica_e_valida_id_token(self, id_token_str):
        """Verify id_token signature with Google's public keys and return the claims dict."""
        claims = id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            self.client_id,
        )
        if claims['aud'] != self.client_id:
            raise ValueError('Token audience does not match client_id')
        return claims

    def verifica_whitelist(self, email):
        """Raise PermissionError if the email is not ALLOWED_EMAIL (single-user whitelist)."""
        if email != self.allowed_email:
            raise PermissionError(f'Access denied: "{email}" is not an authorized account.')
        return True

    def crea_sessione_sicura(self, email):
        """Create a permanent 30-day server-side session for the authenticated user."""
        session.permanent = True
        session['user_email'] = email
        session['authenticated'] = True


def login_required(f):
    """Decorator that blocks unauthenticated requests.

    - /api/* paths → 401 JSON
    - all other paths → redirect to /login
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated') or not session.get('user_email'):
            if request.path.startswith('/api'):
                return {'error': 'Unauthenticated', 'login': '/login'}, 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function
