"""Fixed-window rate-limit counters in the cache.

Each window has its own key (``...:<window index>``), so a counter can never
outlive or be stretched past its window, and the expiry is re-set after
``incr`` (DatabaseCache's incr rewrites the row with the default 300 s timeout).
"""

import hashlib
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache


def _time() -> float:
    return time.time()


def _cache_key(scope: str, key: str, secs: int, now: float) -> str:
    digest = hashlib.sha256((settings.SECRET_KEY + key).encode()).hexdigest()
    return f"rl:{scope}:{digest}:{int(now // secs)}"


def _secs(window: timedelta) -> int:
    return max(1, int(window.total_seconds()))


def allow(scope: str, key: str, limit: int, window: timedelta) -> bool:
    secs = _secs(window)
    cache_key = _cache_key(scope, key, secs, _time())
    cache.add(cache_key, 0, timeout=secs)
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.add(cache_key, 1, timeout=secs)
        count = 1
    cache.touch(cache_key, secs)
    return count <= limit


def exceeded(scope: str, key: str, limit: int, window: timedelta) -> bool:
    """True once `allow` has counted at least `limit` hits in the current window (no increment)."""
    secs = _secs(window)
    return (cache.get(_cache_key(scope, key, secs, _time())) or 0) >= limit
