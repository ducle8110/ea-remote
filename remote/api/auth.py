"""Authentication helpers."""
from functools import wraps
from flask import request, jsonify, session, redirect, url_for
from remote.models import User


def require_api_key(f):
    """Decorator: authenticate EA requests by X-API-Key header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check header first, then JSON body (MQL5 WebRequest sends in body)
        api_key = request.headers.get('X-API-Key', '')
        if not api_key:
            data = request.get_json(silent=True) or {}
            api_key = data.get('api_key', '')
        if not api_key:
            return jsonify({'error': 'Missing API key'}), 401

        user = User.query.filter_by(api_key=api_key, is_active=True).first()
        if not user:
            return jsonify({'error': 'Invalid API key'}), 401

        return f(user, *args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator: require admin session for dashboard routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('dashboard.login'))
        return f(*args, **kwargs)
    return decorated
