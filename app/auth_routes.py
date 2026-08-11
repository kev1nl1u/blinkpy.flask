"""Google auth routes: /login, /login/redirect, /auth/google/token, /oauth2callback, /logout."""

from flask import (
    Blueprint, redirect, session, request, jsonify, current_app,
    make_response, url_for, render_template,
)
from app.auth import GoogleOAuth2Manager

import requests as _requests

bp = Blueprint('auth', __name__)
oauth2_manager = GoogleOAuth2Manager()


@bp.route('/login', methods=['GET'])
def login():
    """Login page whose Google button posts an ID token to /auth/google/token.

    A full-page redirect to Google can't be the default any more. An installed
    PWA on iOS keeps its own cookie store, so the leg through accounts.google.com
    comes back in the *browser's* store: `oauth2_state` is missing there (the
    "possible CSRF attack" error) and the session cookie would be planted in the
    wrong store even if the check passed. Handing the ID token to a same-origin
    fetch keeps the whole exchange inside whichever store the page runs in.
    """
    if session.get('authenticated'):
        return redirect('/')
    response = make_response(
        render_template('login.html', google_client_id=oauth2_manager.client_id)
    )
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


@bp.route('/login/redirect', methods=['GET'])
def login_redirect():
    """Classic authorization-code redirect — fallback when the button can't run."""
    url_autorizzazione, state = oauth2_manager.genera_url_login()
    session['oauth2_state'] = state
    session.modified = True
    current_app.logger.info('OAuth2 redirect login flow started')
    response = make_response(redirect(url_autorizzazione))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


@bp.route('/auth/google/token', methods=['POST'])
def google_token():
    """Create the session from an ID token collected by Google Identity Services.

    There is no `state` to check here and none is needed: the token is signed by
    Google and carries our client_id as its audience, and it arrives on a
    same-origin JSON POST, which a cross-site page cannot forge — that content
    type forces a CORS preflight this app never answers. A replayed token still
    has to clear the whitelist.
    """
    payload = request.get_json(silent=True) or {}
    credential = payload.get('credential')
    if not credential:
        return jsonify({'error': 'Missing credential'}), 400

    try:
        claims = oauth2_manager.decodifica_e_valida_id_token(credential)
    except ValueError as e:
        current_app.logger.warning('Rejected Google credential: %s', e)
        return jsonify({'error': 'Invalid Google credential'}), 401

    if not claims.get('email_verified'):
        return jsonify({'error': 'Unverified Google email'}), 403

    email = claims.get('email')
    try:
        oauth2_manager.verifica_whitelist(email)
    except PermissionError as e:
        current_app.logger.warning('Access denied for email: %s', email)
        return jsonify({'error': str(e)}), 403

    oauth2_manager.crea_sessione_sicura(email)
    current_app.logger.info('Authenticated via Google Identity Services: %s', email)
    return jsonify({'success': True})


@bp.route('/oauth2callback', methods=['GET'])
def oauth2callback():
    try:
        errore_google = request.args.get('error')
        if errore_google:
            current_app.logger.warning('User denied consent: %s', errore_google)
            return jsonify({'error': 'Authentication rejected by user'}), 403

        codice = request.args.get('code')
        if not codice:
            return jsonify({'error': 'Missing authorization code'}), 400

        state_ricevuto = request.args.get('state')
        try:
            oauth2_manager.verifica_state(state_ricevuto)
        except ValueError as e:
            current_app.logger.error('CSRF check failed: %s', e)
            return jsonify({'error': str(e)}), 403

        token_response = oauth2_manager.scambia_codice_con_token(codice)
        id_token_str = token_response.get('id_token')
        if not id_token_str:
            raise ValueError('id_token missing from Google response')

        claims = oauth2_manager.decodifica_e_valida_id_token(id_token_str)
        email = claims.get('email')
        nome = claims.get('name', 'User')

        try:
            oauth2_manager.verifica_whitelist(email)
        except PermissionError as e:
            current_app.logger.warning('Access denied for email: %s', email)
            return jsonify({'error': str(e)}), 403

        current_app.logger.info('Authenticated: %s (%s)', nome, email)
        oauth2_manager.crea_sessione_sicura(email)
        return redirect('/')

    except _requests.RequestException as e:
        current_app.logger.exception('Google communication error: %s', e)
        return jsonify({'error': 'Error communicating with Google', 'details': str(e)}), 502

    except Exception as e:
        current_app.logger.exception('OAuth2 callback error: %s', e)
        return jsonify({'error': 'Authentication error', 'details': str(e)}), 500


@bp.route('/logout')
def logout():
    session.clear()
    response = make_response(redirect(url_for('auth.login')))
    response.delete_cookie(
        current_app.config.get('SESSION_COOKIE_NAME', 'session'),
        path='/',
        samesite=current_app.config.get('SESSION_COOKIE_SAMESITE', 'Lax'),
    )
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response
