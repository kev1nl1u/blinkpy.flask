"""OAuth 2.0 routes: /login, /oauth2callback, /logout."""

from flask import Blueprint, redirect, session, request, jsonify, current_app, make_response, url_for
from app.auth import GoogleOAuth2Manager

import requests as _requests

bp = Blueprint('auth', __name__)
oauth2_manager = GoogleOAuth2Manager()


@bp.route('/login', methods=['GET'])
def login():
    url_autorizzazione, state = oauth2_manager.genera_url_login()
    session['oauth2_state'] = state
    session.modified = True
    current_app.logger.info('OAuth2 login flow started')
    return redirect(url_autorizzazione)


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
