"""Dashboard page routes (Jinja2 rendered)."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from remote.config import Config as AppConfig
from remote.api.auth import require_admin
from remote.models import db, User, EventLog

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == AppConfig.ADMIN_USERNAME and password == AppConfig.ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('dashboard.index'))
        flash('Sai username hoặc password', 'error')
    return render_template('login.html')


@dashboard_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('dashboard.login'))


@dashboard_bp.route('/')
@require_admin
def index():
    """Main dashboard - all users overview."""
    users = User.query.filter_by(is_active=True).all()
    return render_template('dashboard.html', users=users)


@dashboard_bp.route('/user/<int:user_id>')
@require_admin
def user_detail(user_id):
    """Single user config + status page."""
    user = User.query.get_or_404(user_id)
    return render_template('user_detail.html', user=user)


@dashboard_bp.route('/user/new')
@require_admin
def new_user():
    """Create new user form."""
    return render_template('new_user.html')


@dashboard_bp.route('/logs')
@require_admin
def logs():
    """Event log viewer."""
    events = EventLog.query.order_by(EventLog.created_at.desc()).limit(200).all()
    return render_template('logs.html', events=events)
