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

    # Fetch pending commands, send once then auto-ack
    now = datetime.now(timezone.utc)
    pending = Command.query.filter_by(
        user_id=user.id, acknowledged=False
    ).order_by(Command.created_at).all()

    commands = []
    for c in pending:
        cmd_data = {'id': c.id, 'type': c.cmd_type}
        if c.payload and c.payload != '{}':
            try:
                parsed = json.loads(c.payload)
                if parsed:
                    cmd_data['params'] = parsed
            except json.JSONDecodeError:
                pass
        commands.append(cmd_data)
        # Mark as acknowledged immediately (fire-and-forget)
        c.acknowledged = True
        c.ack_at = now

    # Include desired config if it differs from EA's current
    config = Config.query.filter_by(user_id=user.id).first()
    desired_config = config.to_dict() if config else {}

    db.session.commit()

    return jsonify({
        'status': 'ok',
        'commands': commands,
        'config': desired_config,
    })
