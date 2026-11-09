import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache


def _cache_key(scope: str, key: str) -> str:
    digest = hashlib.sha256((settings.SECRET_KEY + key).encode()).hexdigest()
    return f"rl:{scope}:{digest}"


def allow(scope: str, key: str, limit: int, window: timedelta) -> bool:
    cache_key = _cache_key(scope, key)
    cache.add(cache_key, 0, timeout=int(window.total_seconds()))
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.add(cache_key, 1, timeout=int(window.total_seconds()))
        count = 1
    return count <= limit


def exceeded(scope: str, key: str, limit: int) -> bool:
    """True once `allow` has counted at least `limit` hits in the current window (no increment)."""
    return (cache.get(_cache_key(scope, key)) or 0) >= limit
