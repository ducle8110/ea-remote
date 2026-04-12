"""SQLAlchemy models for Remote Control."""
import json
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
    """Dynamic config per user. Schema parsed from uploaded MQ5 file."""
    __tablename__ = 'configs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)

    # Kill switch — always present regardless of EA type
    trading_enabled = db.Column(db.Boolean, default=True)

    # Param schema parsed from MQ5: [{"name","type","default","comment","enum_value"}, ...]
    param_schema = db.Column(db.Text, default='[]')

    # Desired config: what admin wants EA to run
    desired_config = db.Column(db.Text, default='{}')

    # EA reported config: what EA is actually running (from heartbeat)
    ea_reported_config = db.Column(db.Text, default='{}')

    # Admin custom param grouping: {"GroupName": ["Param1","Param2"], ...}
    param_groups = db.Column(db.Text, default='{}')

    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    def get_schema(self) -> list:
        try:
            return json.loads(self.param_schema or '[]')
        except json.JSONDecodeError:
            return []

    def set_schema(self, params: list):
        self.param_schema = json.dumps(params, ensure_ascii=False)

    def get_desired(self) -> dict:
        try:
            cfg = json.loads(self.desired_config or '{}')
        except json.JSONDecodeError:
            cfg = {}
        cfg['trading_enabled'] = self.trading_enabled
        return cfg

    def set_desired(self, data: dict):
        data = dict(data)
        if 'trading_enabled' in data:
            self.trading_enabled = bool(data.pop('trading_enabled'))
        self.desired_config = json.dumps(data, ensure_ascii=False)

    def get_ea_reported(self) -> dict:
        try:
            return json.loads(self.ea_reported_config or '{}')
        except json.JSONDecodeError:
            return {}

    def set_ea_reported(self, data: dict):
        self.ea_reported_config = json.dumps(data, ensure_ascii=False)

    def get_groups(self) -> dict:
        try:
            return json.loads(self.param_groups or '{}')
        except json.JSONDecodeError:
            return {}

    def set_groups(self, groups: dict):
        self.param_groups = json.dumps(groups, ensure_ascii=False)

    def to_dict(self) -> dict:
        return self.get_desired()


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
