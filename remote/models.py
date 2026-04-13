"""SQLAlchemy models for Remote Control."""
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(64), unique=True, index=True, nullable=False)
    ea_version = db.Column(db.String(20), default='')
    account_number = db.Column(db.BigInteger, default=0)
    broker = db.Column(db.String(100), default='')
    symbol = db.Column(db.String(20), default='')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    note = db.Column(db.Text, default='')

    # Uploaded tool files
    tool_filename = db.Column(db.String(200), default='')   # VD: v91_PartialTP
    tool_mq5 = db.Column(db.LargeBinary, nullable=True)     # MQ5 source file
    tool_ex5 = db.Column(db.LargeBinary, nullable=True)     # EX5 compiled file

    config = db.relationship('Config', backref='user', uselist=False,
                             cascade='all, delete-orphan')
    heartbeat = db.relationship('Heartbeat', backref='user', uselist=False,
                                cascade='all, delete-orphan')
    commands = db.relationship('Command', backref='user', lazy='dynamic',
                               cascade='all, delete-orphan')
    events = db.relationship('EventLog', backref='user', lazy='dynamic',
                             cascade='all, delete-orphan')


class Config(db.Model):
    """Desired config per user. EA pulls this via heartbeat response."""
    __tablename__ = 'configs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)

    # Trading
    fixed_lot = db.Column(db.Float, default=0.01)
    step_pip = db.Column(db.Float, default=150.0)
    max_per_side = db.Column(db.Integer, default=50)
    max_spread_pip = db.Column(db.Float, default=50.0)
    ema_fast = db.Column(db.Integer, default=10)
    ema_slow = db.Column(db.Integer, default=50)

    # Take Profit
    cluster_tp_usd = db.Column(db.Float, default=1.5)
    partial_tp_mode = db.Column(db.Integer, default=0)
    partial_tp_usd = db.Column(db.Float, default=1.0)
    partial_tp_same_dir = db.Column(db.Integer, default=3)

    # Weekend
    enable_weekend_hedge = db.Column(db.Boolean, default=True)
    hours_before_close = db.Column(db.Integer, default=2)

    # Safety
    max_drawdown_percent = db.Column(db.Float, default=50.0)
    enforce_step_buy = db.Column(db.Boolean, default=True)
    enforce_step_sell = db.Column(db.Boolean, default=True)
    auto_enforce_step = db.Column(db.Boolean, default=True)
    enforce_on_pct = db.Column(db.Integer, default=50)
    enforce_off_pct = db.Column(db.Integer, default=25)

    # Remote control
    trading_enabled = db.Column(db.Boolean, default=True)

    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    # All configurable param names (for serialization)
    PARAM_NAMES = [
        'fixed_lot', 'step_pip', 'max_per_side', 'max_spread_pip',
        'ema_fast', 'ema_slow', 'cluster_tp_usd', 'partial_tp_mode',
        'partial_tp_usd', 'partial_tp_same_dir', 'enable_weekend_hedge',
        'hours_before_close', 'max_drawdown_percent', 'enforce_step_buy',
        'enforce_step_sell', 'auto_enforce_step', 'enforce_on_pct',
        'enforce_off_pct', 'trading_enabled',
    ]

    def to_dict(self):
        return {name: getattr(self, name) for name in self.PARAM_NAMES}


class Heartbeat(db.Model):
    """Latest status from each EA. One row per user, upserted."""
    __tablename__ = 'heartbeats'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    balance = db.Column(db.Float, default=0)
    equity = db.Column(db.Float, default=0)
    profit = db.Column(db.Float, default=0)
    dd_pct = db.Column(db.Float, default=0)
    buy_count = db.Column(db.Integer, default=0)
    sell_count = db.Column(db.Integer, default=0)
    total_lots_buy = db.Column(db.Float, default=0)
    total_lots_sell = db.Column(db.Float, default=0)
    spread_pip = db.Column(db.Float, default=0)
    hedge_active = db.Column(db.Boolean, default=False)
    ea_version = db.Column(db.String(20), default='')
    magic = db.Column(db.Integer, default=0)
    server_time = db.Column(db.String(30), default='')
    last_seen = db.Column(db.DateTime, default=utcnow)
    current_config = db.Column(db.Text, default='{}')  # JSON snapshot


class Command(db.Model):
    """Pending commands to send to EA via heartbeat response."""
    __tablename__ = 'commands'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cmd_type = db.Column(db.String(50), nullable=False)  # update_config, disable, enable, close_all
    payload = db.Column(db.Text, default='{}')  # JSON
    created_at = db.Column(db.DateTime, default=utcnow)
    acknowledged = db.Column(db.Boolean, default=False)
    ack_at = db.Column(db.DateTime, nullable=True)


class EventLog(db.Model):
    """Audit trail."""
    __tablename__ = 'event_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False)
    detail = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=utcnow)
