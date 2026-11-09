"""Rate-limit counters on the real DatabaseCache: full-length windows, no reset by incr."""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import connection

from web import ratelimit
from web.ratelimit import allow, exceeded

pytestmark = pytest.mark.django_db
T0 = 1_800_000_000.0  # aligned to an hour boundary below
HOUR = timedelta(hours=1)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    cache.clear()
    clock = {"now": T0 - (T0 % 3600)}
    monkeypatch.setattr(ratelimit, "_time", lambda: clock["now"])
    return clock


def _expiry_seconds():
    with connection.cursor() as cur:
        cur.execute("SELECT EXTRACT(EPOCH FROM (expires - now())) FROM unitpricer_cache")
        return [float(r[0]) for r in cur.fetchall()]


def test_stored_expiry_is_the_full_window_after_incr():
    for _ in range(3):
        allow("x", "k", 10, HOUR)
    (remaining,) = _expiry_seconds()
    assert 3500 < remaining <= 3600  # not Django's default 300 s


def test_limit_holds_beyond_five_minutes_within_window(_clock):
    for _ in range(5):
        assert allow("y", "k", 5, HOUR)
    _clock["now"] += 30 * 60  # half an hour later, same window
    assert allow("y", "k", 5, HOUR) is False
    assert exceeded("y", "k", 5, HOUR) is True
    _clock["now"] += 31 * 60  # next window
    assert exceeded("y", "k", 5, HOUR) is False
    assert allow("y", "k", 5, HOUR) is True


def test_feedback_and_waitlist_style_limits_hold_after_ten_minutes(_clock):
    for _ in range(5):
        allow("waitlist-ip", "1.2.3.4", 5, HOUR)
    _clock["now"] += 10 * 60
    assert allow("waitlist-ip", "1.2.3.4", 5, HOUR) is False
