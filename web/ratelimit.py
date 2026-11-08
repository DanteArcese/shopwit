import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache


def allow(scope: str, key: str, limit: int, window: timedelta) -> bool:
    digest = hashlib.sha256((settings.SECRET_KEY + key).encode()).hexdigest()
    cache_key = f"rl:{scope}:{digest}"
    cache.add(cache_key, 0, timeout=int(window.total_seconds()))
    try:
        count = cache.incr(cache_key)
    except ValueError:  # expired between add and incr
        cache.add(cache_key, 1, timeout=int(window.total_seconds()))
        count = 1
    return count <= limit
