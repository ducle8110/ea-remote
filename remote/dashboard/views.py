"""Dashboard page routes (Jinja2 rendered)."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from remote.config import Config as AppConfig
from remote.api.auth import require_admin
from remote.models import db, User, EventLog, Slave

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
    return render_template('dashboard.html', users=[])


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


# ----------------------------------------------------------------------------
# Copy-trade slave management
# ----------------------------------------------------------------------------

@dashboard_bp.route('/user/<int:user_id>/slaves')
@require_admin
def user_slaves(user_id):
    """List slaves của 1 master."""
    user = User.query.get_or_404(user_id)
    slaves = Slave.query.filter_by(master_user_id=user_id)\
        .order_by(Slave.created_at.desc()).all()
    return render_template('slaves_list.html', user=user, slaves=slaves)


@dashboard_bp.route('/user/<int:user_id>/slave/new')
@require_admin
def slave_new(user_id):
    """Form tạo slave mới — hiển thị token+secret 1 lần sau khi create."""
    user = User.query.get_or_404(user_id)
    return render_template('slave_new.html', user=user)


@dashboard_bp.route('/slave/<int:slave_id>')
@require_admin
def slave_detail(slave_id):
    """Slave detail: edit config, revoke, rotate secret."""
    slave = Slave.query.get_or_404(slave_id)
    master = User.query.get(slave.master_user_id)
    return render_template('slave_detail.html', slave=slave, master=master)
