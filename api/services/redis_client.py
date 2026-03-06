"""
Shared Redis connection pool.

Every module that needs Redis should import :func:`get_redis_client` instead of
calling ``redis.Redis.from_url()`` directly.  The underlying
:class:`redis.ConnectionPool` is created once per process and reused across all
callers, eliminating per-call connection overhead.
"""
from __future__ import annotations

import threading

import redis

from api.settings import settings

_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> redis.ConnectionPool:
    """Return the process-wide ``ConnectionPool``, creating it on first use.

    Thread-safe: a :class:`threading.Lock` guards first-time creation so that
    concurrent Celery worker threads cannot race on initialisation.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            # Double-checked locking: re-test after acquiring the lock.
            if _pool is None:
                _pool = redis.ConnectionPool.from_url(
                    settings.redis_url,
                    decode_responses=True,
                )
    return _pool


def get_redis_client() -> redis.Redis:
    """Return a ``redis.Redis`` instance backed by the shared pool."""
    return redis.Redis(connection_pool=_get_pool())


def reset_pool() -> None:
    """Disconnect and discard the current pool.

    Useful for graceful shutdown or in test teardown so the next call to
    :func:`get_redis_client` will create a fresh pool.
    """
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.disconnect()
            except Exception:
                pass
            _pool = None
