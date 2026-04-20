"""Unified notification dispatcher for Discord + Telegram."""
import requests
from flask import current_app


def notify_all(user, message, level="info"):
    """Send notification to both Discord and Telegram."""
    notify_discord(user, message, level)
    notify_telegram(user, message, level)


def notify_discord(user, message, level="info"):
    """Send Discord webhook notification."""
    webhook_url = current_app.config.get('DISCORD_WEBHOOK_URL', '')
    if not webhook_url:
        return

    color = {"info": 0x3498db, "warning": 0xf39c12, "danger": 0xe74c3c}.get(level, 0x3498db)
    payload = {
        "embeds": [{
            "title": f"{user.name} ({user.account_number})",
            "description": message,
            "color": color,
        }]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        current_app.logger.error(f"Discord notification failed: {e}")


def notify_telegram(user, message, level="info"):
    """Send Telegram notification."""
    bot_token = current_app.config.get('TELEGRAM_BOT_TOKEN', '')
    if not bot_token:
        return

    chat_id = getattr(user, 'telegram_chat_id', None) or current_app.config.get('TELEGRAM_ADMIN_CHAT_ID', '')
    if not chat_id:
        return

    emoji = {"info": "ℹ️", "warning": "⚠️", "danger": "🔴"}.get(level, "ℹ️")
    text = f"{emoji} *{user.name}*\n{message}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        current_app.logger.error(f"Telegram notification failed: {e}")
