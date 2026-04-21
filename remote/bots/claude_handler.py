"""Claude AI handler for natural language Discord interaction."""
import json
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
Ban la tro ly quan ly EA trading tren MT5. Ban giup admin dieu khien EA thong qua cac tool duoc cung cap.

Quy tac:
- Tra loi bang tieng Viet, ngan gon, ro rang.
- Khi user hoi trang thai, su dung tool get_all_status hoac get_user_detail.
- Khi user muon thay doi config (lot, step, TP, DD, ...), su dung tool update_config.
- QUAN TRONG: Khi user muon dong tat ca lenh (close all), ban PHAI hoi xac nhan truoc. Chi goi close_all_positions voi confirmed=true khi user da noi ro "xac nhan", "confirm", "dong y", hoac "ok dong het".
- Khi khong hieu y user, hoi lai cho ro.
- Khong tu y thuc hien hanh dong nguy hiem ma khong co xac nhan.
"""

# ---------------------------------------------------------------------------
# Tool definitions for Anthropic API
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_all_status",
        "description": "Lay trang thai tat ca users/EAs: balance, equity, drawdown, so lenh buy/sell, spread, online/offline.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_users",
        "description": "Liet ke tat ca users dang active voi phien ban EA.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_user_detail",
        "description": "Lay thong tin chi tiet 1 user. Tra ve 2 config: 'ea_current_config' la config THUC TE EA dang chay (uu tien dung cai nay khi tra loi), 'config' la config server mong muon. Neu 2 config khac nhau thi ghi chu cho user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "disable_trading",
        "description": "Tat trading cho 1 user. EA se khong mo lenh moi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user can tat trading"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "enable_trading",
        "description": "Bat trading cho 1 user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user can bat trading"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "close_all_positions",
        "description": (
            "Dong TAT CA lenh cua 1 user. DAY LA HANH DONG NGUY HIEM. "
            "Ban PHAI hoi user xac nhan truoc khi goi tool nay. "
            "Chi goi khi user da noi ro 'xac nhan', 'confirm', hoac 'dong y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user"},
                "confirmed": {
                    "type": "boolean",
                    "description": "True neu user da xac nhan. PHAI la true moi thuc hien.",
                },
            },
            "required": ["user_name", "confirmed"],
        },
    },
    {
        "name": "update_config",
        "description": "Cap nhat config EA cho 1 user. Chi truyen cac tham so can thay doi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user"},
                "fixed_lot": {"type": "number", "description": "Lot size (VD: 0.01, 0.02)"},
                "step_pip": {"type": "number", "description": "Khoang cach pip giua cac lenh"},
                "max_per_side": {"type": "integer", "description": "So lenh toi da moi huong"},
                "max_spread_pip": {"type": "number", "description": "Spread toi da (pip)"},
                "cluster_tp_usd": {"type": "number", "description": "Take profit cluster (USD)"},
                "max_drawdown_percent": {"type": "number", "description": "Drawdown toi da (%)"},
                "ema_fast": {"type": "integer", "description": "EMA nhanh"},
                "ema_slow": {"type": "integer", "description": "EMA cham"},
                "partial_tp_mode": {"type": "integer", "description": "Che do TP tung phan (0=off, 1=on)"},
                "partial_tp_usd": {"type": "number", "description": "TP tung phan (USD)"},
                "partial_tp_same_dir": {"type": "integer", "description": "So lenh cung huong de kich hoat partial TP"},
                "dual_switch_high": {"type": "integer", "description": "Dual mode: chenh lech buy/sell >= X thi chuyen CloseFar (default 50)"},
                "dual_switch_low": {"type": "integer", "description": "Dual mode: chenh lech buy/sell <= Y thi chuyen Combo21 (default 25)"},
                "enable_weekend_hedge": {"type": "boolean", "description": "Bat/tat hedge cuoi tuan"},
                "hours_before_close": {"type": "integer", "description": "So gio truoc close market de hedge"},
                "enforce_step_buy": {"type": "boolean", "description": "Enforce step cho buy"},
                "enforce_step_sell": {"type": "boolean", "description": "Enforce step cho sell"},
                "auto_enforce_step": {"type": "boolean", "description": "Tu dong enforce step khi DD cao"},
                "enforce_on_pct": {"type": "integer", "description": "DD % de bat enforce step"},
                "enforce_off_pct": {"type": "integer", "description": "DD % de tat enforce step"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "get_logs",
        "description": "Lay lich su event logs. Co the loc theo user va loai event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Ten user (tuy chon)"},
                "event_type": {
                    "type": "string",
                    "description": "Loai event: config_change, disable_trading, enable_trading, close_all, alert_offline, alert_drawdown",
                },
                "limit": {"type": "integer", "description": "So log toi da (mac dinh 10)"},
            },
            "required": [],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers — each returns a dict
# ---------------------------------------------------------------------------

def _find_user(name):
    from remote.models import User
    return User.query.filter_by(name=name, is_active=True).first()


def _handle_get_all_status(_input):
    from remote.models import User
    from datetime import datetime, timezone
    import json as _json

    users = User.query.filter_by(is_active=True).all()
    now = datetime.now(timezone.utc)
    result = []
    for u in users:
        hb = u.heartbeat
        if hb and hb.last_seen:
            delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
            online = delta < 60
        else:
            online = False

        # Get trading_enabled from EA's actual config if available
        trading_enabled = True
        if hb and hb.current_config:
            try:
                ea_cfg = _json.loads(hb.current_config)
                trading_enabled = ea_cfg.get('trading_enabled', True)
            except (ValueError, TypeError):
                trading_enabled = u.config.trading_enabled if u.config else True
        elif u.config:
            trading_enabled = u.config.trading_enabled

        result.append({
            "name": u.name,
            "online": online,
            "balance": hb.balance if hb else 0,
            "equity": hb.equity if hb else 0,
            "profit": hb.profit if hb else 0,
            "dd_pct": hb.dd_pct if hb else 0,
            "buy_count": hb.buy_count if hb else 0,
            "sell_count": hb.sell_count if hb else 0,
            "spread_pip": hb.spread_pip if hb else 0,
            "trading_enabled": trading_enabled,
        })
    if not result:
        return {"message": "Khong co user nao"}
    return {"users": result}


def _handle_list_users(_input):
    from remote.models import User
    users = User.query.filter_by(is_active=True).all()
    if not users:
        return {"message": "Khong co user nao"}
    return {"users": [{"name": u.name, "ea_version": u.ea_version or "?"} for u in users]}


def _handle_get_user_detail(inp):
    from remote.models import Config as CfgModel
    from datetime import datetime, timezone
    import json as _json

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Khong tim thay user '{inp['user_name']}'"}

    hb = user.heartbeat
    online = False
    if hb and hb.last_seen:
        delta = (datetime.now(timezone.utc) - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
        online = delta < 60

    config_dict = user.config.to_dict() if user.config else {}

    # Parse current_config from heartbeat (actual config EA is running)
    ea_config = {}
    if hb and hb.current_config:
        try:
            ea_config = _json.loads(hb.current_config)
        except (ValueError, TypeError):
            pass

    return {
        "name": user.name,
        "account_number": user.account_number,
        "broker": user.broker,
        "symbol": user.symbol,
        "online": online,
        "balance": hb.balance if hb else 0,
        "equity": hb.equity if hb else 0,
        "profit": hb.profit if hb else 0,
        "dd_pct": hb.dd_pct if hb else 0,
        "buy_count": hb.buy_count if hb else 0,
        "sell_count": hb.sell_count if hb else 0,
        "spread_pip": hb.spread_pip if hb else 0,
        "hedge_active": hb.hedge_active if hb else False,
        "config": config_dict,
        "ea_current_config": ea_config,
    }


def _handle_disable_trading(inp):
    from remote.models import db, Command, EventLog
    from datetime import datetime, timezone

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Khong tim thay user '{inp['user_name']}'"}

    if user.config:
        user.config.trading_enabled = False

    # Cancel pending enable commands
    now = datetime.now(timezone.utc)
    for old_cmd in Command.query.filter_by(
        user_id=user.id, cmd_type='enable_trading', acknowledged=False
    ).all():
        old_cmd.acknowledged = True
        old_cmd.ack_at = now

    cmd = Command(user_id=user.id, cmd_type='disable_trading', payload='{}')
    db.session.add(cmd)
    db.session.add(EventLog(
        user_id=user.id,
        event_type='disable_trading',
        detail='Triggered via Discord AI chat',
    ))
    db.session.commit()
    return {"success": True, "message": f"Da tat trading cho {user.name}"}


def _handle_enable_trading(inp):
    from remote.models import db, Command, EventLog
    from datetime import datetime, timezone

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Khong tim thay user '{inp['user_name']}'"}

    if user.config:
        user.config.trading_enabled = True

    # Cancel pending disable commands
    now = datetime.now(timezone.utc)
    for old_cmd in Command.query.filter_by(
        user_id=user.id, cmd_type='disable_trading', acknowledged=False
    ).all():
        old_cmd.acknowledged = True
        old_cmd.ack_at = now

    cmd = Command(user_id=user.id, cmd_type='enable_trading', payload='{}')
    db.session.add(cmd)
    db.session.add(EventLog(
        user_id=user.id,
        event_type='enable_trading',
        detail='Triggered via Discord AI chat',
    ))
    db.session.commit()
    return {"success": True, "message": f"Da bat trading cho {user.name}"}


def _handle_close_all(inp):
    from remote.models import db, Command, EventLog

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Khong tim thay user '{inp['user_name']}'"}

    if not inp.get("confirmed"):
        return {"error": "Chua xac nhan. Hay hoi user xac nhan truoc khi dong lenh."}

    cmd = Command(user_id=user.id, cmd_type='close_all', payload='{}')
    db.session.add(cmd)
    db.session.add(EventLog(
        user_id=user.id,
        event_type='close_all',
        detail='Triggered via Discord AI chat',
    ))
    db.session.commit()
    return {"success": True, "message": f"Da gui lenh CLOSE ALL cho {user.name}"}


def _handle_update_config(inp):
    from remote.models import db, Config as CfgModel, Command, EventLog

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Khong tim thay user '{inp['user_name']}'"}

    config = user.config
    if not config:
        config = CfgModel(user_id=user.id)
        db.session.add(config)

    changed = {}
    for key in CfgModel.PARAM_NAMES:
        if key in inp and key != "user_name":
            old_val = getattr(config, key)
            new_val = inp[key]
            if old_val != new_val:
                setattr(config, key, new_val)
                changed[key] = {"old": old_val, "new": new_val}

    if not changed:
        return {"message": "Khong co gi thay doi"}

    cmd = Command(
        user_id=user.id,
        cmd_type='update_config',
        payload=json.dumps({k: v["new"] for k, v in changed.items()}),
    )
    db.session.add(cmd)
    db.session.add(EventLog(
        user_id=user.id,
        event_type='config_change',
        detail=json.dumps(changed, ensure_ascii=False),
    ))
    db.session.commit()
    return {"success": True, "changed": changed}


def _handle_get_logs(inp):
    from remote.models import EventLog

    query = EventLog.query
    if inp.get("user_name"):
        user = _find_user(inp["user_name"])
        if not user:
            return {"error": f"Khong tim thay user '{inp['user_name']}'"}
        query = query.filter_by(user_id=user.id)
    if inp.get("event_type"):
        query = query.filter_by(event_type=inp["event_type"])

    limit = inp.get("limit", 10)
    logs = query.order_by(EventLog.created_at.desc()).limit(limit).all()
    return {
        "logs": [
            {
                "event_type": l.event_type,
                "user_name": l.user.name if l.user else None,
                "detail": l.detail,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]
    }


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------
_HANDLERS = {
    "get_all_status": _handle_get_all_status,
    "list_users": _handle_list_users,
    "get_user_detail": _handle_get_user_detail,
    "disable_trading": _handle_disable_trading,
    "enable_trading": _handle_enable_trading,
    "close_all_positions": _handle_close_all,
    "update_config": _handle_update_config,
    "get_logs": _handle_get_logs,
}


def execute_tool(app, tool_name, tool_input):
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    with app.app_context():
        return json.dumps(handler(tool_input), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Conversation management & main entry point
# ---------------------------------------------------------------------------
_conversations = {}  # channel_id -> list of messages


def process_message(app, user_message, channel_id):
    """Process a natural language message through Claude with tools.

    Returns the text response to send back to Discord.
    """
    import anthropic

    api_key = app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return "Claude AI chua duoc cau hinh (thieu ANTHROPIC_API_KEY)."

    model = app.config.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514')
    max_history = app.config.get('CLAUDE_MAX_HISTORY', 20)

    client = anthropic.Anthropic(api_key=api_key)

    # Get/create conversation history for this channel
    history = _conversations.setdefault(channel_id, [])
    history.append({"role": "user", "content": user_message})

    # Trim old messages
    if len(history) > max_history:
        _conversations[channel_id] = history[-max_history:]
        history = _conversations[channel_id]

    messages = list(history)

    try:
        # Tool use loop (max 5 iterations)
        for _ in range(5):
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # No tools — extract text and return
                text = "".join(b.text for b in response.content if b.type == "text")
                history.append({"role": "assistant", "content": text})
                return text or "(Khong co phan hoi)"

            # Execute tools and feed results back
            # Store raw content for the API (needs ContentBlock objects serialized)
            assistant_content = [
                {"type": b.type, **({"text": b.text} if b.type == "text" else {"id": b.id, "name": b.name, "input": b.input})}
                for b in response.content
            ]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in tool_use_blocks:
                result = execute_tool(app, block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

        # Loop exhausted — grab last text if any
        text = "".join(b.text for b in response.content if b.type == "text")
        history.append({"role": "assistant", "content": text or "Xin loi, khong the xu ly yeu cau nay."})
        return history[-1]["content"]

    except Exception as e:
        log.exception("Claude handler error")
        return f"Loi khi goi Claude API: {e}"


def clear_history(channel_id):
    """Clear conversation history for a channel."""
    _conversations.pop(channel_id, None)
