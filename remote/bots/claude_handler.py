"""Claude AI handler for natural language Discord interaction."""
import json
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
Bạn là trợ lý quản lý EA trading trên MT5. Bạn giúp admin điều khiển EA thông qua các tool được cung cấp.

Quy tắc:
- Trả lời bằng tiếng Việt, ngắn gọn, rõ ràng.
- Khi user hỏi trạng thái, sử dụng tool get_all_status hoặc get_user_detail.
- Khi user muốn thay đổi config (lot, step, TP, DD, ...), sử dụng tool update_config.
- QUAN TRỌNG: Khi user muốn đóng tất cả lệnh (close all), bạn PHẢI hỏi xác nhận trước. Chỉ gọi close_all_positions với confirmed=true khi user đã nói rõ "xác nhận", "confirm", "đồng ý", hoặc "ok đóng hết".
- Khi không hiểu ý user, hỏi lại cho rõ.
- Không tự ý thực hiện hành động nguy hiểm mà không có xác nhận.
"""

# ---------------------------------------------------------------------------
# Tool definitions for Anthropic API
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_all_status",
        "description": "Lấy trạng thái tất cả users/EAs: balance, equity, drawdown, số lệnh buy/sell, spread, online/offline.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_users",
        "description": "Liệt kê tất cả users đang active với phiên bản EA.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_user_detail",
        "description": "Lấy thông tin chi tiết 1 user. Trả về 2 config: 'ea_current_config' là config THỰC TẾ EA đang chạy (ưu tiên dùng cái này khi trả lời), 'config' là config server mong muốn. Nếu 2 config khác nhau thì ghi chú cho user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "disable_trading",
        "description": "Tắt trading cho 1 user. EA sẽ không mở lệnh mới.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user cần tắt trading"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "enable_trading",
        "description": "Bật trading cho 1 user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user cần bật trading"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "close_all_positions",
        "description": (
            "Đóng TẤT CẢ lệnh của 1 user. ĐÂY LÀ HÀNH ĐỘNG NGUY HIỂM. "
            "Bạn PHẢI hỏi user xác nhận trước khi gọi tool này. "
            "Chỉ gọi khi user đã nói rõ 'xác nhận', 'confirm', hoặc 'đồng ý'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user"},
                "confirmed": {
                    "type": "boolean",
                    "description": "True nếu user đã xác nhận. PHẢI là true mới thực hiện.",
                },
            },
            "required": ["user_name", "confirmed"],
        },
    },
    {
        "name": "update_config",
        "description": "Cập nhật config EA cho 1 user. Chỉ truyền các tham số cần thay đổi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user"},
                "fixed_lot": {"type": "number", "description": "Lot size (VD: 0.01, 0.02)"},
                "step_pip": {"type": "number", "description": "Khoảng cách pip giữa các lệnh"},
                "max_per_side": {"type": "integer", "description": "Số lệnh tối đa mỗi hướng"},
                "max_spread_pip": {"type": "number", "description": "Spread tối đa (pip)"},
                "cluster_tp_usd": {"type": "number", "description": "Take profit cluster (USD)"},
                "max_drawdown_percent": {"type": "number", "description": "Drawdown tối đa (%)"},
                "ema_fast": {"type": "integer", "description": "EMA nhanh"},
                "ema_slow": {"type": "integer", "description": "EMA chậm"},
                "partial_tp_mode": {"type": "integer", "description": "Chế độ TP từng phần (0=off, 1=on)"},
                "partial_tp_usd": {"type": "number", "description": "TP từng phần (USD)"},
                "partial_tp_same_dir": {"type": "integer", "description": "Số lệnh cùng hướng để kích hoạt partial TP"},
                "partial_tp_close_pct": {"type": "integer", "description": "% volume cắt mỗi lần gặm lot CloseFar (0=tự động)"},
                "dual_switch_high": {"type": "integer", "description": "Dual mode: chênh lệch buy/sell >= X thì chuyển CloseFar (default 50)"},
                "dual_switch_low": {"type": "integer", "description": "Dual mode: chênh lệch buy/sell <= Y thì chuyển Combo21 (default 25)"},
                "enable_weekend_hedge": {"type": "boolean", "description": "Bật/tắt hedge cuối tuần"},
                "hours_before_close": {"type": "integer", "description": "Số giờ trước close market để hedge"},
                "enforce_step_buy": {"type": "boolean", "description": "Enforce step cho buy"},
                "enforce_step_sell": {"type": "boolean", "description": "Enforce step cho sell"},
                "auto_enforce_step": {"type": "boolean", "description": "Tự động enforce step khi DD cao"},
                "enforce_on_pct": {"type": "integer", "description": "DD % để bật enforce step"},
                "enforce_off_pct": {"type": "integer", "description": "DD % để tắt enforce step"},
            },
            "required": ["user_name"],
        },
    },
    {
        "name": "get_logs",
        "description": "Lấy lịch sử event logs. Có thể lọc theo user và loại event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_name": {"type": "string", "description": "Tên user (tùy chọn)"},
                "event_type": {
                    "type": "string",
                    "description": "Loại event: config_change, disable_trading, enable_trading, close_all, alert_offline, alert_drawdown",
                },
                "limit": {"type": "integer", "description": "Số log tối đa (mặc định 10)"},
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
        return {"message": "Không có user nào"}
    return {"users": result}


def _handle_list_users(_input):
    from remote.models import User
    users = User.query.filter_by(is_active=True).all()
    if not users:
        return {"message": "Không có user nào"}
    return {"users": [{"name": u.name, "ea_version": u.ea_version or "?"} for u in users]}


def _handle_get_user_detail(inp):
    from remote.models import Config as CfgModel
    from datetime import datetime, timezone
    import json as _json

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Không tìm thấy user '{inp['user_name']}'"}

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
        return {"error": f"Không tìm thấy user '{inp['user_name']}'"}

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
    return {"success": True, "message": f"Đã tắt trading cho {user.name}"}


def _handle_enable_trading(inp):
    from remote.models import db, Command, EventLog
    from datetime import datetime, timezone

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Không tìm thấy user '{inp['user_name']}'"}

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
    return {"success": True, "message": f"Đã bật trading cho {user.name}"}


def _handle_close_all(inp):
    from remote.models import db, Command, EventLog

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Không tìm thấy user '{inp['user_name']}'"}

    if not inp.get("confirmed"):
        return {"error": "Chưa xác nhận. Hãy hỏi user xác nhận trước khi đóng lệnh."}

    cmd = Command(user_id=user.id, cmd_type='close_all', payload='{}')
    db.session.add(cmd)
    db.session.add(EventLog(
        user_id=user.id,
        event_type='close_all',
        detail='Triggered via Discord AI chat',
    ))
    db.session.commit()
    return {"success": True, "message": f"Đã gửi lệnh CLOSE ALL cho {user.name}"}


def _handle_update_config(inp):
    from remote.models import db, Config as CfgModel, Command, EventLog

    user = _find_user(inp["user_name"])
    if not user:
        return {"error": f"Không tìm thấy user '{inp['user_name']}'"}

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
        return {"message": "Không có gì thay đổi"}

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
            return {"error": f"Không tìm thấy user '{inp['user_name']}'"}
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
        return "Claude AI chưa được cấu hình (thiếu ANTHROPIC_API_KEY)."

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
                return text or "(Không có phản hồi)"

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
        history.append({"role": "assistant", "content": text or "Xin lỗi, không thể xử lý yêu cầu này."})
        return history[-1]["content"]

    except Exception as e:
        log.exception("Claude handler error")
        return f"Lỗi khi gọi Claude API: {e}"


def clear_history(channel_id):
    """Clear conversation history for a channel."""
    _conversations.pop(channel_id, None)
