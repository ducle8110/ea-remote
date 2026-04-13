"""Discord bot with command handling + Claude AI chat."""
import asyncio
import threading
import discord
from discord.ext import commands as dc_commands
from flask import Flask
from remote.bots.notifications import notify_discord
from remote.bots.claude_handler import process_message, clear_history

_bot_thread = None


def start_discord_bot(app: Flask):
    """Start Discord bot in a background thread."""
    token = app.config.get('DISCORD_BOT_TOKEN', '')
    if not token:
        app.logger.info("Discord bot disabled (no DISCORD_BOT_TOKEN)")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    bot = dc_commands.Bot(command_prefix='!', intents=intents)

    @bot.event
    async def on_ready():
        app.logger.info(f"Discord bot logged in as {bot.user}")

    @bot.command(name='status')
    async def cmd_status(ctx):
        """Show all users status. Usage: !status"""
        with app.app_context():
            from remote.models import User
            from datetime import datetime, timezone
            users = User.query.filter_by(is_active=True).all()
            now = datetime.now(timezone.utc)

            embed = discord.Embed(title="EA Status Overview", color=0x4f8cff)
            for u in users:
                hb = u.heartbeat
                if hb and hb.last_seen:
                    delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()
                    icon = "🟢" if delta < 60 else "🔴"
                    val = (f"${hb.balance:.0f} | DD {hb.dd_pct:.1f}%\n"
                           f"B{hb.buy_count}/S{hb.sell_count} | "
                           f"Spread {hb.spread_pip:.1f}")
                else:
                    icon = "⚫"
                    val = "No data"
                embed.add_field(name=f"{icon} {u.name}", value=val, inline=True)

            if not users:
                embed.description = "No users yet"
            await ctx.send(embed=embed)

    @bot.command(name='disable')
    async def cmd_disable(ctx, *, name: str = None):
        """Disable trading. Usage: !disable <user_name>"""
        if not name:
            await ctx.send("Usage: `!disable <user_name>`")
            return
        with app.app_context():
            from remote.models import db, User, Command
            user = User.query.filter_by(name=name, is_active=True).first()
            if not user:
                await ctx.send(f"❌ User `{name}` not found")
                return
            if user.config:
                user.config.trading_enabled = False
            cmd = Command(user_id=user.id, cmd_type='disable_trading', payload='{}')
            db.session.add(cmd)
            db.session.commit()
            await ctx.send(f"⛔ Trading **DISABLED** for **{name}**")

    @bot.command(name='enable')
    async def cmd_enable(ctx, *, name: str = None):
        """Enable trading. Usage: !enable <user_name>"""
        if not name:
            await ctx.send("Usage: `!enable <user_name>`")
            return
        with app.app_context():
            from remote.models import db, User, Command
            user = User.query.filter_by(name=name, is_active=True).first()
            if not user:
                await ctx.send(f"❌ User `{name}` not found")
                return
            if user.config:
                user.config.trading_enabled = True
            cmd = Command(user_id=user.id, cmd_type='enable_trading', payload='{}')
            db.session.add(cmd)
            db.session.commit()
            await ctx.send(f"✅ Trading **ENABLED** for **{name}**")

    @bot.command(name='closeall')
    async def cmd_closeall(ctx, *, name: str = None):
        """Close all positions. Usage: !closeall <user_name> CONFIRM"""
        if not name:
            await ctx.send("Usage: `!closeall <user_name> CONFIRM`")
            return
        parts = name.rsplit(' ', 1)
        if len(parts) < 2 or parts[1] != 'CONFIRM':
            await ctx.send(f"⚠️ Type `!closeall {parts[0]} CONFIRM` to confirm")
            return
        user_name = parts[0]
        with app.app_context():
            from remote.models import db, User, Command
            user = User.query.filter_by(name=user_name, is_active=True).first()
            if not user:
                await ctx.send(f"❌ User `{user_name}` not found")
                return
            cmd = Command(user_id=user.id, cmd_type='close_all', payload='{}')
            db.session.add(cmd)
            db.session.commit()
            await ctx.send(f"🔴 **CLOSE ALL** sent to **{user_name}**")

    @bot.command(name='users')
    async def cmd_users(ctx):
        """List all users. Usage: !users"""
        with app.app_context():
            from remote.models import User
            users = User.query.filter_by(is_active=True).all()
            if not users:
                await ctx.send("No users")
                return
            lines = [f"**{u.name}** (v{u.ea_version or '?'})" for u in users]
            await ctx.send("**Users:**\n" + "\n".join(lines))

    @bot.command(name='clear')
    async def cmd_clear(ctx):
        """Clear Claude AI conversation history. Usage: !clear"""
        clear_history(str(ctx.channel.id))
        await ctx.send("Da xoa lich su hoi thoai AI.")

    @bot.event
    async def on_message(message):
        # Ignore own messages
        if message.author == bot.user:
            return

        # Process ! commands first
        await bot.process_commands(message)

        # Don't trigger AI for ! commands
        if message.content.startswith('!'):
            return

        # Only respond when @mentioned
        if not bot.user or bot.user not in message.mentions:
            return

        app.logger.info(f"[Claude AI] Mentioned by {message.author}: {message.content[:100]}")

        # Skip if no API key configured
        if not app.config.get('ANTHROPIC_API_KEY', ''):
            app.logger.warning("[Claude AI] ANTHROPIC_API_KEY not set, skipping")
            await message.reply("Claude AI chua duoc cau hinh (thieu ANTHROPIC_API_KEY).")
            return

        # Strip bot mention from message content
        content = message.content
        if bot.user:
            content = content.replace(f'<@{bot.user.id}>', '').strip()
        if not content:
            return

        try:
            async with message.channel.typing():
                reply = await asyncio.to_thread(
                    process_message, app, content, str(message.channel.id)
                )

            # Send response, chunked if > 2000 chars
            for i in range(0, len(reply), 2000):
                if i == 0:
                    await message.reply(reply[i:i + 2000])
                else:
                    await message.channel.send(reply[i:i + 2000])
        except Exception as e:
            app.logger.exception(f"[Claude AI] Error: {e}")
            await message.reply(f"Loi: {e}")

    def run_bot():
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app.logger.info("Discord bot connecting...")
            loop.run_until_complete(bot.start(token))
        except Exception as e:
            app.logger.error(f"Discord bot crashed: {e}")
            import traceback
            app.logger.error(traceback.format_exc())

    global _bot_thread
    _bot_thread = threading.Thread(target=run_bot, daemon=True, name="discord-bot")
    _bot_thread.start()
    app.logger.info("Discord bot thread started")
