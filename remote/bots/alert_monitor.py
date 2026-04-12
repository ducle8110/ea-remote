"""Background alert monitor - checks for offline EAs and DD thresholds."""
import threading
import time
from datetime import datetime, timezone
from flask import Flask


def start_alert_monitor(app: Flask):
    """Start background thread that monitors EA status and sends alerts."""

    # Track alert state to avoid spam
    _offline_alerted = set()  # user IDs already alerted as offline
    _dd_alerted = {}          # user_id -> last alerted DD level

    def monitor_loop():
        while True:
            time.sleep(15)
            try:
                with app.app_context():
                    from remote.models import db, User, EventLog
                    from remote.bots.notifications import notify_all

                    users = User.query.filter_by(is_active=True).all()
                    now = datetime.now(timezone.utc)
                    timeout = app.config.get('OFFLINE_TIMEOUT_SEC', 60)
                    dd_levels = app.config.get('DD_ALERT_LEVELS', [30, 40, 50])

                    for u in users:
                        hb = u.heartbeat
                        if not hb or not hb.last_seen:
                            continue

                        delta = (now - hb.last_seen.replace(tzinfo=timezone.utc)).total_seconds()

                        # --- Offline alert ---
                        if delta > timeout:
                            if u.id not in _offline_alerted:
                                _offline_alerted.add(u.id)
                                msg = f"EA OFFLINE - last seen {int(delta)}s ago"
                                notify_all(u, msg, "danger")
                                db.session.add(EventLog(
                                    user_id=u.id, event_type='alert_offline', detail=msg))
                        else:
                            # Reconnect alert
                            if u.id in _offline_alerted:
                                _offline_alerted.discard(u.id)
                                msg = "EA ONLINE - reconnected"
                                notify_all(u, msg, "info")
                                db.session.add(EventLog(
                                    user_id=u.id, event_type='alert_reconnect', detail=msg))

                        # --- DD alert ---
                        for level in sorted(dd_levels):
                            if hb.dd_pct >= level:
                                last_level = _dd_alerted.get(u.id, 0)
                                if level > last_level:
                                    _dd_alerted[u.id] = level
                                    msg = f"DD WARNING: {hb.dd_pct:.1f}% (equity ${hb.equity:.2f})"
                                    notify_all(u, msg, "warning" if level < 50 else "danger")
                                    db.session.add(EventLog(
                                        user_id=u.id, event_type='alert_drawdown',
                                        detail=f"DD {hb.dd_pct:.1f}% >= {level}%"))
                            else:
                                # Reset if DD recovered below this level
                                if _dd_alerted.get(u.id, 0) >= level:
                                    _dd_alerted[u.id] = max(0, level - 10)

                    db.session.commit()
            except Exception as e:
                app.logger.error(f"Alert monitor error: {e}")

    thread = threading.Thread(target=monitor_loop, daemon=True)
    thread.start()
    app.logger.info("Alert monitor started (checking every 15s)")
