"""Flask app factory for Remote Control server."""
from flask import Flask
from remote.config import Config as AppConfig
from remote.models import db


def create_app():
    app = Flask(__name__,
                template_folder='dashboard/templates',
                static_folder='dashboard/static')
    app.config.from_object(AppConfig)

    # Fix Render PostgreSQL URL (postgres:// -> postgresql://)
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if uri.startswith('postgres://'):
        app.config['SQLALCHEMY_DATABASE_URI'] = uri.replace('postgres://', 'postgresql://', 1)

    db.init_app(app)

    # Register blueprints
    from remote.api.ea_routes import ea_bp
    from remote.api.admin_routes import admin_bp
    from remote.dashboard.views import dashboard_bp

    app.register_blueprint(ea_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dashboard_bp)

    # Create tables
    with app.app_context():
        db.create_all()

    # Start background services (only in main process, not reloader)
    import os
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        from remote.bots.alert_monitor import start_alert_monitor
        start_alert_monitor(app)

        from remote.bots.telegram_bot import start_telegram_bot
        start_telegram_bot(app)

        from remote.bots.discord_bot import start_discord_bot
        start_discord_bot(app)

    return app
