"""API endpoints that the EA calls."""
import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from remote.models import db, Heartbeat, Command, Config, EventLog
from remote.api.auth import require_api_key

ea_bp = Blueprint('ea', __name__)


@ea_bp.route('/api/ea/heartbeat', methods=['POST'])
@require_api_key
def heartbeat(user):
    """EA sends status, receives pending commands."""
    data = request.get_json(silent=True) or {}

    # Upsert heartbeat
    hb = Heartbeat.query.filter_by(user_id=user.id).first()
    if not hb:
        hb = Heartbeat(user_id=user.id)
        db.session.add(hb)

    hb.balance = data.get('balance', 0)
    hb.equity = data.get('equity', 0)
    hb.profit = data.get('profit', 0)
    hb.dd_pct = data.get('dd_pct', 0)
    hb.buy_count = data.get('buy_count', 0)
    hb.sell_count = data.get('sell_count', 0)
    hb.total_lots_buy = data.get('total_lots_buy', 0)
    hb.total_lots_sell = data.get('total_lots_sell', 0)
    hb.spread_pip = data.get('spread_pip', 0)
    hb.hedge_active = data.get('hedge_active', False)
    hb.ea_version = data.get('ea_version', '')
    hb.magic = data.get('magic', 0)
    hb.server_time = data.get('server_time', '')
    hb.last_seen = datetime.now(timezone.utc)
    hb.current_config = json.dumps(data.get('current_config', {}))

    # Update user info from heartbeat
    if data.get('account'):
        user.account_number = data['account']
    if data.get('broker'):
        user.broker = data['broker']
    if data.get('symbol'):
        user.symbol = data['symbol']
    if data.get('ea_version'):
        user.ea_version = data['ea_version']

    # Process acknowledged command IDs
    ack_ids = data.get('ack_command_ids', [])
    if ack_ids:
        now = datetime.now(timezone.utc)
        for cmd in Command.query.filter(
            Command.id.in_(ack_ids),
            Command.user_id == user.id
        ).all():
            cmd.acknowledged = True
            cmd.ack_at = now

    # Auto-ack commands based on EA reported state
    ea_cfg = data.get('current_config', {})
    now = datetime.now(timezone.utc)
    unacked = Command.query.filter_by(
        user_id=user.id, acknowledged=False
    ).order_by(Command.created_at).all()

    commands = []
    for c in unacked:
        # Auto-ack if EA state already matches the command
        if c.cmd_type == 'disable_trading' and ea_cfg.get('trading_enabled') == False:
            c.acknowledged = True
            c.ack_at = now
            continue
        if c.cmd_type == 'enable_trading' and ea_cfg.get('trading_enabled') == True:
            c.acknowledged = True
            c.ack_at = now
            continue
        if c.cmd_type == 'update_config':
            # Auto-ack if all changed params match EA reported
            try:
                payload = json.loads(c.payload) if c.payload else {}
            except json.JSONDecodeError:
                payload = {}
            if payload and all(ea_cfg.get(k) == v for k, v in payload.items()):
                c.acknowledged = True
                c.ack_at = now
                continue

        cmd_data = {'id': c.id, 'type': c.cmd_type}
        if c.payload and c.payload != '{}':
            try:
                parsed = json.loads(c.payload)
                if parsed:
                    cmd_data['params'] = parsed
            except json.JSONDecodeError:
                pass
        commands.append(cmd_data)

    # Include desired config if it differs from EA's current
    config = Config.query.filter_by(user_id=user.id).first()
    desired_config = config.to_dict() if config else {}

    db.session.commit()

    return jsonify({
        'status': 'ok',
        'commands': commands,
        'config': desired_config,
    })
