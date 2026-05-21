"""Background retention loop — xoá TradeEvent cũ để DB không phình.

Chạy mỗi 6 giờ. Xoá events có `ts < now - retention_days * 86400`.
Mặc định retention 7 ngày — tương đương Go relay cũ.

Pattern thread giống alert_monitor.py (single daemon thread, app_context per tick).
"""
import threading
import time
from flask import Flask

_DEFAULT_RETENTION_DAYS = 7
_TICK_INTERVAL_SEC = 6 * 3600  # 6 hours


def start_retention_loop(app: Flask):
    """Khởi động background thread retention. Daemon, dừng khi app exit."""

    def retention_tick():
        from remote.models import db, TradeEvent
        retention_days = app.config.get('COPYTRADE_RETENTION_DAYS', _DEFAULT_RETENTION_DAYS)
        cutoff = int(time.time()) - retention_days * 86400
        deleted = (TradeEvent.query
                   .filter(TradeEvent.ts < cutoff)
                   .delete(synchronize_session=False))
        db.session.commit()
        if deleted:
            app.logger.info(f"Retention: deleted {deleted} trade_events older than {retention_days}d")

    def loop():
        # Chạy ngay lần đầu để don sạch backlog nếu có
        time.sleep(60)  # đợi 1 phút sau khi app start để tránh race với migration
        while True:
            try:
                with app.app_context():
                    retention_tick()
            except Exception as e:
                app.logger.error(f"Retention loop error: {e}")
            time.sleep(_TICK_INTERVAL_SEC)

    thread = threading.Thread(target=loop, daemon=True, name='retention_loop')
    thread.start()
    app.logger.info(f"Retention loop started (tick {_TICK_INTERVAL_SEC // 3600}h, "
                    f"keep {_DEFAULT_RETENTION_DAYS}d)")
