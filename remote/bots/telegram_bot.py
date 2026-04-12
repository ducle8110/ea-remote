"""Telegram bot with command handling."""
import threading
from flask import Flask
from remote.bots.notifications import notify_telegram

# Telegram bot chạy trong background thread
_bot_thread = None


def start_telegram_bot(app: Flask):
    """Start Telegram bot polling in a background thread."""
    token = app.config.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        app.logger.info("Telegram bot disabled (no token)")
        return

    try:
        from telegram import Update
        from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
    except ImportError:
        app.logger.warning("python-telegram-bot not installed, Telegram bot disabled")
        return

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show all users status."""
        with app.app_context():
            from remote.models import User, Heartbeat
            from datetime import datetime, timezone
            users = User.query.filter_by(is_active=True).all()
            now = datetime.now(timezone.utc)
            lines = ["*EA Status Overview*\n"]
            for u in users:
                hb = u.heartbeat
                if hb and hb.last_seen:
                    delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
                    status = "🟢" if delta < 60 else "🔴"
                    lines.append(
                        f"{status} *{u.name}* | "
                        f"${hb.balance:.0f} | "
                        f"DD {hb.dd_pct:.1f}% | "
                        f"B{hb.buy_count}/S{hb.sell_count}"
                    )
                else:
                    lines.append(f"⚫ *{u.name}* | No data")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Disable trading for a user."""
        if not context.args:
            await update.message.reply_text("Usage: /disable <user_name>")
            return
        name = " ".join(context.args)
        with app.app_context():
            from remote.models import db, User, Command
            import json
            user = User.query.filter_by(name=name, is_active=True).first()
            if not user:
                await update.message.reply_text(f"User '{name}' not found")
                return
            if user.config:
                user.config.trading_enabled = False
            cmd = Command(user_id=user.id, cmd_type='disable_trading', payload='{}')
            db.session.add(cmd)
            db.session.commit()
            await update.message.reply_text(f"⛔ Trading DISABLED for {name}")

    async def cmd_enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enable trading for a user."""
        if not context.args:
            await update.message.reply_text("Usage: /enable <user_name>")
            return
        name = " ".join(context.args)
        with app.app_context():
            from remote.models import db, User, Command
            import json
            user = User.query.filter_by(name=name, is_active=True).first()
            if not user:
                await update.message.reply_text(f"User '{name}' not found")
                return
            if user.config:
                user.config.trading_enabled = True
            cmd = Command(user_id=user.id, cmd_type='enable_trading', payload='{}')
            db.session.add(cmd)
            db.session.commit()
            await update.message.reply_text(f"✅ Trading ENABLED for {name}")

    def run_bot():
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            application = ApplicationBuilder().token(token).build()
            application.add_handler(CommandHandler("status", cmd_status))
            application.add_handler(CommandHandler("disable", cmd_disable))
            application.add_handler(CommandHandler("enable", cmd_enable))

            app.logger.info("Telegram bot connecting...")
            loop.run_until_complete(application.run_polling(drop_pending_updates=True))
        except Exception as e:
            app.logger.error(f"Telegram bot crashed: {e}")
            import traceback
            app.logger.error(traceback.format_exc())

    global _bot_thread
    _bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    _bot_thread.start()
    app.logger.info("Telegram bot thread started")
