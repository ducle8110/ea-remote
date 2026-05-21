"""HMAC-SHA256 authentication cho copy-trade endpoints.

Hai lớp auth:
- X-Slave-Token (slave) / X-Master-Token (master, = User.api_key): identity. Lookup DB.
- X-Ts + X-Sig: HMAC SHA-256 over `ts\\nmethod\\nURI\\nbody` ký với entity hmac_secret.
- Replay window: ±30 giây.

Cache token → (entity_info, expires_at), TTL 1h. Admin phải gọi flush_token() khi revoke
hoặc rotate secret, nếu không có thể có window đến 1h slave bị revoke vẫn vào được.

Cache _max_event_id_per_master: skip DB query cho empty polls (slave đã catch up rồi
vẫn poll 1s/lần). Updated bởi /push, queried bởi /pull.

Thread safety: waitress là single-process threaded → 1 cache instance share giữa threads.
RLock đủ. Không cần Redis.
"""
import hmac
import hashlib
import time
from collections import namedtuple
from functools import wraps
from threading import RLock

from flask import request, jsonify

from remote.models import User, Slave

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
_TOKEN_TTL_SEC = 3600        # cache validity per token
_TS_SKEW_SEC = 30            # HMAC replay window (match Go relay tsSkewSec)

# ----------------------------------------------------------------------------
# Cache structures
# ----------------------------------------------------------------------------
SlaveInfo = namedtuple('SlaveInfo', ['id', 'master_user_id', 'token', 'hmac_secret'])
MasterInfo = namedtuple('MasterInfo', ['id', 'token', 'hmac_secret'])

# token → (SlaveInfo|MasterInfo, expires_at_unix)
_token_cache: dict = {}
_cache_lock = RLock()

# master_user_id → max event id ever seen by /push.
# Slave's /pull check this first → nếu since >= max → return empty không query DB.
_max_event_id_cache: dict = {}
_max_event_id_lock = RLock()


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def flush_token(token: str) -> None:
    """Invalidate cache entry. Admin gọi sau khi revoke slave hoặc rotate secret."""
    with _cache_lock:
        _token_cache.pop(token, None)


def get_max_event_id(master_user_id: int) -> int:
    """Latest event id master này đã push. 0 nếu chưa thấy event nào (cold start)."""
    with _max_event_id_lock:
        return _max_event_id_cache.get(master_user_id, 0)


def set_max_event_id(master_user_id: int, event_id: int) -> None:
    """Update sau khi insert TradeEvent thành công. No-op nếu event_id cũ hơn."""
    with _max_event_id_lock:
        if event_id > _max_event_id_cache.get(master_user_id, 0):
            _max_event_id_cache[master_user_id] = event_id


# ----------------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------------
def _check_ts(ts_str: str) -> bool:
    try:
        ts = int(ts_str)
    except (ValueError, TypeError):
        return False
    return abs(int(time.time()) - ts) <= _TS_SKEW_SEC


def _verify_hmac(secret_hex: str, ts_str: str, method: str, uri: str, body: str,
                 received_sig: str) -> bool:
    try:
        key = bytes.fromhex(secret_hex)
    except ValueError:
        return False
    msg = f"{ts_str}\n{method}\n{uri}\n{body}".encode('utf-8')
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_sig)


def _signed_uri() -> str:
    """Match EA's UrlPath() — path + query string, KHÔNG trailing '?' nếu không có query.

    EA (Go relay) dùng r.URL.RequestURI() = '/path' hoặc '/path?k=v'.
    Flask request.full_path luôn có trailing '?' kể cả không query → KHÔNG match EA.
    Phải build thủ công.
    """
    if request.query_string:
        return request.path + '?' + request.query_string.decode('utf-8')
    return request.path


def _lookup_slave(token: str):
    """Cache-first lookup. Return SlaveInfo hoặc None nếu invalid/inactive."""
    now = int(time.time())
    with _cache_lock:
        entry = _token_cache.get(token)
        if entry:
            info, expires_at = entry
            if isinstance(info, SlaveInfo) and now < expires_at:
                return info
            _token_cache.pop(token, None)

    slave = Slave.query.filter_by(token=token, is_active=True).first()
    if slave is None:
        return None
    info = SlaveInfo(
        id=slave.id,
        master_user_id=slave.master_user_id,
        token=slave.token,
        hmac_secret=slave.hmac_secret,
    )
    with _cache_lock:
        _token_cache[token] = (info, now + _TOKEN_TTL_SEC)
    return info


def _lookup_master(token: str):
    """Master token = User.api_key. Cần User.hmac_secret set (copy-trade enabled)."""
    now = int(time.time())
    with _cache_lock:
        entry = _token_cache.get(token)
        if entry:
            info, expires_at = entry
            if isinstance(info, MasterInfo) and now < expires_at:
                return info
            _token_cache.pop(token, None)

    user = User.query.filter_by(api_key=token, is_active=True).first()
    if user is None or not user.hmac_secret:
        return None
    info = MasterInfo(id=user.id, token=user.api_key, hmac_secret=user.hmac_secret)
    with _cache_lock:
        _token_cache[token] = (info, now + _TOKEN_TTL_SEC)
    return info


def _auth_common(token_header: str, lookup_fn):
    """Shared auth flow. Returns (info, error_response). Exactly one is None."""
    token = request.headers.get(token_header, '')
    ts_str = request.headers.get('X-Ts', '')
    sig = request.headers.get('X-Sig', '')

    if not token or not ts_str or not sig:
        return None, (jsonify({'error': f'Missing {token_header} / X-Ts / X-Sig'}), 401)
    if not _check_ts(ts_str):
        return None, (jsonify({'error': 'Timestamp skew too large'}), 401)

    info = lookup_fn(token)
    if info is None:
        return None, (jsonify({'error': 'Invalid token or not authorized'}), 401)

    body = request.get_data(as_text=True) or ''
    if not _verify_hmac(info.hmac_secret, ts_str, request.method, _signed_uri(), body, sig):
        return None, (jsonify({'error': 'Bad signature'}), 401)

    return info, None


# ----------------------------------------------------------------------------
# Decorators — dùng cho routes trong api/copytrade_routes.py
# ----------------------------------------------------------------------------
def require_slave_auth(f):
    """Verify X-Slave-Token + HMAC. Wrapped fn nhận SlaveInfo làm first arg."""
    @wraps(f)
    def decorated(*args, **kwargs):
        info, err = _auth_common('X-Slave-Token', _lookup_slave)
        if err is not None:
            return err
        return f(info, *args, **kwargs)
    return decorated


def require_master_auth(f):
    """Verify X-Master-Token (= User.api_key) + HMAC. Wrapped fn nhận MasterInfo."""
    @wraps(f)
    def decorated(*args, **kwargs):
        info, err = _auth_common('X-Master-Token', _lookup_master)
        if err is not None:
            return err
        return f(info, *args, **kwargs)
    return decorated
