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

    # Fetch pending commands
    pending = Command.query.filter_by(
        user_id=user.id, acknowledged=False
    ).order_by(Command.created_at).all()

    commands = []
    for c in pending:
        cmd_data = {'id': c.id, 'type': c.cmd_type}
        if c.payload:
            try:
                cmd_data['params'] = json.loads(c.payload)
            except json.JSONDecodeError:
                cmd_data['params'] = {}
        commands.append(cmd_data)

    # Update EA reported config on Config model
    config = Config.query.filter_by(user_id=user.id).first()
    ea_current_config = data.get('current_config', {})
    if config and ea_current_config:
        config.set_ea_reported(ea_current_config)

    desired_config = config.to_dict() if config else {}

    db.session.commit()

    return jsonify({
        'status': 'ok',
        'commands': commands,
        'config': desired_config,
    })
