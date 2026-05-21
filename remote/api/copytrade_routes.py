"""Copy-trade endpoints — master/slave HTTP relay.

Wire protocol portable từ Go relay (`copytrade/relay/main.go`):
- POST /api/copytrade/push          [master auth]  insert TradeEvent
- POST /api/copytrade/master_state  [master auth]  upsert MasterState
- GET  /api/copytrade/pull?...      [slave auth]   query events id > since
- GET  /api/copytrade/snapshot?...  [slave auth]   master state + last_event_id
- GET  /api/copytrade/health        [public]       liveness probe

Tối ưu critical trong /pull: kiểm tra _max_event_id_cache trước query DB.
Slave poll 1s/lần, 99% trả empty (đã catch up). Cache short-circuit để khỏi
đập DB cho mọi empty poll.
"""
import json

from flask import Blueprint, request, jsonify

from remote.models import db, MasterState, TradeEvent
from remote.auth_hmac import (
    require_master_auth, require_slave_auth,
    get_max_event_id, set_max_event_id,
)

copytrade_bp = Blueprint('copytrade', __name__)


@copytrade_bp.route('/api/copytrade/health', methods=['GET'])
def health():
    """Public liveness — cloudflared và monitoring dùng."""
    return jsonify({'ok': True})


@copytrade_bp.route('/api/copytrade/push', methods=['POST'])
@require_master_auth
def push(master):
    """Master push 1 trade event (open/close/modify)."""
    data = request.get_json(silent=True) or {}
    ts = data.get('ts')
    event_type = data.get('type')
    if ts is None or not event_type:
        return jsonify({'error': 'Missing ts/type in body'}), 400

    # Lưu raw body làm payload — slave nhận lại nguyên dạng EA gửi
    body_str = request.get_data(as_text=True)
    event = TradeEvent(
        master_user_id=master.id,
        ts=int(ts),
        type=event_type,
        payload=body_str,
    )
    db.session.add(event)
    db.session.commit()

    # Update cache để /pull của các slave thấy event ngay (skip DB query)
    set_max_event_id(master.id, event.id)
    return jsonify({'ok': True, 'id': event.id})


@copytrade_bp.route('/api/copytrade/master_state', methods=['POST'])
@require_master_auth
def master_state(master):
    """Master heartbeat balance/equity (slave dùng để tính lot ratio)."""
    data = request.get_json(silent=True) or {}
    balance = data.get('balance')
    equity = data.get('equity')
    ts = data.get('ts')
    if balance is None or equity is None or ts is None:
        return jsonify({'error': 'Missing balance/equity/ts'}), 400

    # PK của MasterState = master_user_id
    state = db.session.get(MasterState, master.id)
    if state is None:
        state = MasterState(master_user_id=master.id,
                            balance=balance, equity=equity, ts=int(ts))
        db.session.add(state)
    else:
        state.balance = balance
        state.equity = equity
        state.ts = int(ts)
    db.session.commit()
    return jsonify({'ok': True})


@copytrade_bp.route('/api/copytrade/pull', methods=['GET'])
@require_slave_auth
def pull(slave):
    """Slave poll event id > since.

    CRITICAL: check _max_event_id_cache TRƯỚC query DB.
    Nếu since >= max → return empty không query (cover 99% empty polls).
    """
    try:
        since = int(request.args.get('since', 0))
    except (ValueError, TypeError):
        since = 0
    try:
        limit = int(request.args.get('limit', 200))
    except (ValueError, TypeError):
        limit = 200
    limit = max(1, min(500, limit))

    master_id = slave.master_user_id

    # Fast path: cache nói "không có event mới" → return empty không DB query
    cached_max = get_max_event_id(master_id)
    if cached_max > 0 and since >= cached_max:
        return jsonify({'events': []})

    # Slow path: query DB (có thể là cold start hoặc có event thật)
    rows = (TradeEvent.query
            .filter(TradeEvent.master_user_id == master_id,
                    TradeEvent.id > since)
            .order_by(TradeEvent.id.asc())
            .limit(limit)
            .all())

    events_out = []
    new_max = since
    for r in rows:
        # Parse payload string → object để slave EA nhận nested JSON (match Go relay format)
        try:
            payload_obj = json.loads(r.payload)
        except (json.JSONDecodeError, TypeError):
            payload_obj = {}
        events_out.append({
            'id': r.id,
            'ts': r.ts,
            'type': r.type,
            'payload': payload_obj,
        })
        if r.id > new_max:
            new_max = r.id

    # Populate cache nếu cold start → empty polls tiếp theo hit fast path
    if new_max > cached_max:
        set_max_event_id(master_id, new_max)

    return jsonify({'events': events_out})


@copytrade_bp.route('/api/copytrade/snapshot', methods=['GET'])
@require_slave_auth
def snapshot(slave):
    """Slave reconcile lúc attach: lấy master_state + last_event_id để skip lệnh cũ."""
    master_id = slave.master_user_id
    state = db.session.get(MasterState, master_id)
    if state is None:
        return jsonify({'ok': False, 'error': 'no state'})

    # last_event_id: prefer cache (zero DB); fallback DB MAX(id) khi cold start
    last_id = get_max_event_id(master_id)
    if last_id == 0:
        result = (db.session.query(db.func.max(TradeEvent.id))
                  .filter(TradeEvent.master_user_id == master_id)
                  .scalar())
        last_id = int(result or 0)
        if last_id > 0:
            set_max_event_id(master_id, last_id)

    return jsonify({
        'ok': True,
        'master_state': {
            'master_id': str(master_id),     # wire format Go relay: string
            'balance': state.balance,
            'equity': state.equity,
            'ts': state.ts,
        },
        'last_event_id': last_id,
    })
