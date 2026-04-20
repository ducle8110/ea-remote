"""Admin API endpoints for dashboard AJAX calls."""
import json
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from remote.models import db, User, Config, Command, Heartbeat, EventLog
from remote.api.auth import require_admin

admin_bp = Blueprint('admin_api', __name__)


@admin_bp.route('/api/admin/users')
@require_admin
def list_users():
    """List all users with latest heartbeat."""
    users = User.query.filter_by(is_active=True).all()
    now = datetime.now(timezone.utc)
    result = []
    for u in users:
        hb = u.heartbeat
        online = False
        last_seen_ago = None
        if hb and hb.last_seen:
            delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
            online = delta < 60
            last_seen_ago = int(delta)

        result.append({
            'id': u.id,
            'name': u.name,
            'account_number': u.account_number,
            'broker': u.broker,
            'symbol': u.symbol,
            'ea_version': u.ea_version,
            'online': online,
            'last_seen_ago': last_seen_ago,
            'balance': hb.balance if hb else 0,
            'equity': hb.equity if hb else 0,
            'profit': hb.profit if hb else 0,
            'dd_pct': hb.dd_pct if hb else 0,
            'buy_count': hb.buy_count if hb else 0,
            'sell_count': hb.sell_count if hb else 0,
            'spread_pip': hb.spread_pip if hb else 0,
            'hedge_active': hb.hedge_active if hb else False,
            'trading_enabled': u.config.trading_enabled if u.config else True,
        })
    return jsonify(result)


@admin_bp.route('/api/admin/user/<int:user_id>')
@require_admin
def get_user(user_id):
    """Get user detail with config and heartbeat."""
    u = User.query.get_or_404(user_id)
    hb = u.heartbeat
    config = u.config

    # Auto-create config if missing (e.g. after migration)
    if not config:
        config = Config(user_id=u.id)
        # Seed from EA reported config if available
        if hb and hb.current_config:
            try:
                ea_vals = json.loads(hb.current_config)
                for key in Config.PARAM_NAMES:
                    if key in ea_vals:
                        setattr(config, key, ea_vals[key])
            except json.JSONDecodeError:
                pass
        db.session.add(config)
        db.session.commit()

    now = datetime.now(timezone.utc)
    online = False
    if hb and hb.last_seen:
        delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
        online = delta < 60

    ea_config = {}
    if hb and hb.current_config:
        try:
            ea_config = json.loads(hb.current_config)
        except json.JSONDecodeError:
            pass

    return jsonify({
        'id': u.id,
        'name': u.name,
        'api_key': u.api_key,
        'account_number': u.account_number,
        'broker': u.broker,
        'symbol': u.symbol,
        'ea_version': u.ea_version,
        'is_active': u.is_active,
        'note': u.note,
        'tool_filename': u.tool_filename or '',
        'has_mq5': u.tool_mq5 is not None,
        'has_ex5': u.tool_ex5 is not None,
        'online': online,
        'heartbeat': {
            'balance': hb.balance if hb else 0,
            'equity': hb.equity if hb else 0,
            'profit': hb.profit if hb else 0,
            'dd_pct': hb.dd_pct if hb else 0,
            'buy_count': hb.buy_count if hb else 0,
            'sell_count': hb.sell_count if hb else 0,
            'total_lots_buy': hb.total_lots_buy if hb else 0,
            'total_lots_sell': hb.total_lots_sell if hb else 0,
            'spread_pip': hb.spread_pip if hb else 0,
            'hedge_active': hb.hedge_active if hb else False,
            'server_time': hb.server_time if hb else '',
            'last_seen': hb.last_seen.isoformat() if hb and hb.last_seen else None,
        },
        'server_config': config.to_dict() if config else {},
        'ea_config': ea_config,
    })


@admin_bp.route('/api/admin/user/<int:user_id>/config', methods=['PUT'])
@require_admin
def update_config(user_id):
    """Update config for a user. Creates an update_config command."""
    u = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}

    config = u.config
    if not config:
        config = Config(user_id=u.id)
        db.session.add(config)

    changed = {}
    for key in Config.PARAM_NAMES:
        if key in data:
            old_val = getattr(config, key)
            new_val = data[key]
            if old_val != new_val:
                setattr(config, key, new_val)
                changed[key] = {'old': old_val, 'new': new_val}

    if changed:
        # Create command for EA to pick up
        cmd = Command(
            user_id=u.id,
            cmd_type='update_config',
            payload=json.dumps({k: v['new'] for k, v in changed.items()}),
        )
        db.session.add(cmd)

        # Log event
        evt = EventLog(
            user_id=u.id,
            event_type='config_change',
            detail=json.dumps(changed),
        )
        db.session.add(evt)

    db.session.commit()
    return jsonify({'status': 'ok', 'changed': changed})


@admin_bp.route('/api/admin/user/<int:user_id>/command', methods=['POST'])
@require_admin
def send_command(user_id):
    """Send a command to EA (disable, enable, close_all)."""
    u = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    cmd_type = data.get('type', '')

    if cmd_type not in ('disable_trading', 'enable_trading', 'close_all'):
        return jsonify({'error': 'Invalid command type'}), 400

    # For disable/enable, update config and cancel opposite pending commands
    opposite = {'disable_trading': 'enable_trading', 'enable_trading': 'disable_trading'}
    if cmd_type in opposite:
        if u.config:
            u.config.trading_enabled = (cmd_type == 'enable_trading')
        # Ack any pending opposite commands to avoid conflict
        now = datetime.now(timezone.utc)
        for old_cmd in Command.query.filter_by(
            user_id=u.id, cmd_type=opposite[cmd_type], acknowledged=False
        ).all():
            old_cmd.acknowledged = True
            old_cmd.ack_at = now

    cmd = Command(
        user_id=u.id,
        cmd_type=cmd_type,
        payload=json.dumps(data.get('params', {})),
    )
    db.session.add(cmd)

    evt = EventLog(
        user_id=u.id,
        event_type=cmd_type,
        detail=f'Command sent by admin',
    )
    db.session.add(evt)

    db.session.commit()
    return jsonify({'status': 'ok', 'command_id': cmd.id})


@admin_bp.route('/api/admin/bulk/command', methods=['POST'])
@require_admin
def bulk_command():
    """Send enable/disable command to multiple users at once."""
    data = request.get_json(silent=True) or {}
    user_ids = data.get('user_ids', [])
    cmd_type = data.get('type', '')

    if not user_ids:
        return jsonify({'error': 'No users selected'}), 400
    if cmd_type not in ('enable_trading', 'disable_trading'):
        return jsonify({'error': 'Only enable_trading/disable_trading allowed'}), 400

    users = User.query.filter(User.id.in_(user_ids), User.is_active == True).all()
    if not users:
        return jsonify({'error': 'No valid users found'}), 404

    opposite = {'disable_trading': 'enable_trading', 'enable_trading': 'disable_trading'}
    now = datetime.now(timezone.utc)
    processed = 0

    for u in users:
        if u.config:
            u.config.trading_enabled = (cmd_type == 'enable_trading')

        # Cancel opposite pending commands
        for old_cmd in Command.query.filter_by(
            user_id=u.id, cmd_type=opposite[cmd_type], acknowledged=False
        ).all():
            old_cmd.acknowledged = True
            old_cmd.ack_at = now

        db.session.add(Command(
            user_id=u.id,
            cmd_type=cmd_type,
            payload='{}',
        ))
        db.session.add(EventLog(
            user_id=u.id,
            event_type=cmd_type,
            detail='Bulk command by admin',
        ))
        processed += 1

    db.session.commit()
    return jsonify({'status': 'ok', 'processed': processed})


@admin_bp.route('/api/admin/user', methods=['POST'])
@require_admin
def create_user():
    """Create a new user with auto-generated API key."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400

    api_key = uuid.uuid4().hex
    user = User(
        name=name,
        api_key=api_key,
        note=data.get('note', ''),
    )
    db.session.add(user)
    db.session.flush()  # get user.id

    # Create default config
    config = Config(user_id=user.id)
    db.session.add(config)

    evt = EventLog(
        user_id=user.id,
        event_type='user_created',
        detail=f'User "{name}" created',
    )
    db.session.add(evt)

    db.session.commit()
    return jsonify({
        'status': 'ok',
        'id': user.id,
        'api_key': api_key,
    }), 201


@admin_bp.route('/api/admin/user/<int:user_id>/note', methods=['PUT'])
@require_admin
def update_note(user_id):
    """Update user note."""
    u = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    u.note = data.get('note', '')
    db.session.commit()
    return jsonify({'status': 'ok'})


@admin_bp.route('/api/admin/user/<int:user_id>', methods=['DELETE'])
@require_admin
def deactivate_user(user_id):
    """Deactivate a user."""
    u = User.query.get_or_404(user_id)
    u.is_active = False
    db.session.add(EventLog(
        user_id=u.id,
        event_type='user_deactivated',
        detail=f'User "{u.name}" deactivated',
    ))
    db.session.commit()
    return jsonify({'status': 'ok'})


@admin_bp.route('/api/admin/user/<int:user_id>/upload', methods=['POST'])
@require_admin
def upload_tool_files(user_id):
    """Upload MQ5/EX5 tool files for a user."""
    from flask import send_file
    u = User.query.get_or_404(user_id)

    mq5 = request.files.get('mq5')
    ex5 = request.files.get('ex5')

    if not mq5 and not ex5:
        return jsonify({'error': 'No files uploaded'}), 400

    if mq5:
        u.tool_mq5 = mq5.read()
        fname = mq5.filename or ''
        if fname.endswith('.mq5'):
            u.tool_filename = fname[:-4]
    if ex5:
        u.tool_ex5 = ex5.read()
        if not u.tool_filename and ex5.filename:
            fname = ex5.filename
            if fname.endswith('.ex5'):
                u.tool_filename = fname[:-4]

    db.session.add(EventLog(
        user_id=u.id,
        event_type='tool_uploaded',
        detail=f'Files: mq5={"yes" if mq5 else "no"} ex5={"yes" if ex5 else "no"} name={u.tool_filename}',
    ))
    db.session.commit()
    return jsonify({'status': 'ok', 'tool_filename': u.tool_filename})


@admin_bp.route('/api/admin/user/<int:user_id>/download/<file_type>')
@require_admin
def download_tool_file(user_id, file_type):
    """Download MQ5 or EX5 file for a user."""
    import io
    from flask import send_file
    u = User.query.get_or_404(user_id)

    if file_type == 'mq5' and u.tool_mq5:
        return send_file(
            io.BytesIO(u.tool_mq5),
            download_name=f'{u.tool_filename or "tool"}.mq5',
            as_attachment=True,
        )
    elif file_type == 'ex5' and u.tool_ex5:
        return send_file(
            io.BytesIO(u.tool_ex5),
            download_name=f'{u.tool_filename or "tool"}.ex5',
            as_attachment=True,
        )
    return jsonify({'error': 'File not found'}), 404


@admin_bp.route('/api/admin/export/<int:user_id>')
@require_admin
def export_csv(user_id):
    """Export event logs as CSV for a user. Query params: start, end (YYYY-MM-DD), types."""
    import csv
    import io
    from flask import Response

    u = User.query.get_or_404(user_id)

    query = EventLog.query.filter_by(user_id=user_id)

    # Filter by date range
    start = request.args.get('start')
    end = request.args.get('end')
    if start:
        query = query.filter(EventLog.created_at >= datetime.fromisoformat(start).replace(tzinfo=timezone.utc))
    if end:
        end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc).replace(hour=23, minute=59, second=59)
        query = query.filter(EventLog.created_at <= end_dt)

    # Filter by event types (comma-separated)
    types = request.args.get('types', '')
    if types:
        type_list = [t.strip() for t in types.split(',') if t.strip()]
        query = query.filter(EventLog.event_type.in_(type_list))

    logs = query.order_by(EventLog.created_at).all()

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['timestamp', 'event_type', 'detail'])
    for log in logs:
        writer.writerow([
            log.created_at.isoformat() if log.created_at else '',
            log.event_type,
            log.detail,
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={u.name}_logs.csv'},
    )


@admin_bp.route('/api/admin/logs')
@require_admin
def get_logs():
    """Get event logs with optional filters and pagination."""
    user_id = request.args.get('user_id', type=int)
    event_type = request.args.get('event_type', '')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    query = EventLog.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    if event_type:
        query = query.filter_by(event_type=event_type)

    total = query.count()
    logs = query.order_by(EventLog.created_at.desc())\
        .offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'logs': [{
            'id': l.id,
            'user_id': l.user_id,
            'user_name': l.user.name if l.user else None,
            'event_type': l.event_type,
            'detail': l.detail,
            'created_at': l.created_at.isoformat() if l.created_at else None,
        } for l in logs],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })
