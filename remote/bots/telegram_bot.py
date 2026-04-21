"""Telegram bot with command handling + Claude AI chat."""
import asyncio
import threading
from flask import Flask
from remote.bots.notifications import notify_telegram
from remote.bots.claude_handler import process_message, clear_history

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

    async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clear Claude AI conversation history."""
        clear_history(str(update.effective_chat.id))
        await update.message.reply_text("Đã xóa lịch sử hội thoại AI.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle non-command text messages via Claude AI."""
        if not update.message or not update.message.text:
            return

        # Skip if no API key
        if not app.config.get('ANTHROPIC_API_KEY', ''):
            return

        chat_id = str(update.effective_chat.id)
        user_text = update.message.text

        # In group chats, only respond when bot is mentioned by name
        if update.effective_chat.type in ('group', 'supergroup'):
            bot_info = context.bot
            bot_username = (await bot_info.get_me()).username or ''
            if f'@{bot_username}' not in user_text:
                return
            user_text = user_text.replace(f'@{bot_username}', '').strip()

        if not user_text:
            return

        app.logger.info(
            f"[Claude AI TG] {update.effective_user.first_name} "
            f"(chat {chat_id}): {user_text[:100]}"
        )

        try:
            reply = await asyncio.to_thread(
                process_message, app, user_text, f"tg_{chat_id}"
            )
            # Telegram message limit = 4096 chars
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i + 4096])
        except Exception as e:
            app.logger.exception(f"[Claude AI TG] Error: {e}")
            await update.message.reply_text(f"Lỗi: {e}")

    def run_bot():
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            from telegram.ext import MessageHandler, filters
            application = ApplicationBuilder().token(token).build()
            application.add_handler(CommandHandler("status", cmd_status))
            application.add_handler(CommandHandler("disable", cmd_disable))
            application.add_handler(CommandHandler("enable", cmd_enable))
            application.add_handler(CommandHandler("clear", cmd_clear))
            application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND, handle_message
            ))

            app.logger.info("Telegram bot connecting...")
            # Dùng initialize + start_polling thay vì run_polling
            # vì run_polling đăng ký signal handler — không chạy được trong background thread
            loop.run_until_complete(application.initialize())
            loop.run_until_complete(application.updater.start_polling(drop_pending_updates=True))
            loop.run_until_complete(application.start())
            app.logger.info("Telegram bot polling started")
            loop.run_forever()
        except Exception as e:
            app.logger.error(f"Telegram bot crashed: {e}")
            import traceback
            app.logger.error(traceback.format_exc())

    global _bot_thread
    _bot_thread = threading.Thread(target=run_bot, daemon=True, name="telegram-bot")
    _bot_thread.start()
    app.logger.info("Telegram bot thread started")
