"""Flask app factory for Remote Control server."""
from flask import Flask
from remote.config import Config as AppConfig
from remote.models import db


def _migrate_if_needed(app):
    """Add missing columns to configs table if schema is outdated."""
    from sqlalchemy import inspect, text
    from remote.models import Config
    inspector = inspect(db.engine)
    if 'configs' in inspector.get_table_names():
        columns = {c['name'] for c in inspector.get_columns('configs')}
        # Current model expects 'fixed_lot' column; if missing, schema is wrong
        if 'fixed_lot' not in columns:
            app.logger.info("Configs table schema mismatch, recreating...")
            db.session.execute(text('DROP TABLE configs'))
            db.session.commit()
            return
        # Add missing columns with defaults
        col_defaults = {
            'dual_switch_high': ('INTEGER', 50),
            'dual_switch_low': ('INTEGER', 25),
        }
        for col, (col_type, default) in col_defaults.items():
            if col not in columns:
                app.logger.info(f"Adding missing column: {col}")
                db.session.execute(text(
                    f'ALTER TABLE configs ADD COLUMN {col} {col_type} DEFAULT {default}'
                ))
        db.session.commit()


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

    # Create/migrate tables
    with app.app_context():
        _migrate_if_needed(app)
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
