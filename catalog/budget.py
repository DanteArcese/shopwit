"""DB-backed token buckets (``SourceBudget``) shared by every process that calls a source.

``take``, ``backoff`` and ``refund`` are pure; ``DbBudget`` applies them to a row locked with
``SELECT ... FOR UPDATE`` inside one transaction, so two processes or threads can
never spend the same token.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from connectors.live import BudgetExhausted, Stopping

from .models import SourceBudget

BACKOFF_BASE = 5.0  # seconds
BACKOFF_MAX = 900.0  # 15 minutes
BACKOFF_MAX_EXPONENT = 8  # 5 s * 2**8 already exceeds BACKOFF_MAX; never overflows
MAX_FAILURES = 1000  # the counter saturates (the column is a small integer)
NO_REFILL_WAIT = (
    3600.0  # a bucket that never refills is retried hourly, never "forever"
)
EPSILON = (
    1e-9  # float refill arithmetic must not miss a whole token by a rounding error
)


@dataclass(frozen=True)
class Bucket:
    capacity: float
    refill_per_second: float
    tokens: float
    refilled_at: datetime
    daily_limit: int | None
    daily_used: int
    day: date
    blocked_until: datetime | None
    consecutive_failures: int


def _utc_day(now: datetime) -> date:
    return now.astimezone(UTC).date()


def boosted_limit(key: str, stored: int | None, now: datetime) -> int | None:
    """The daily cap in force: the first-week boost for off-product, else the stored cap.

    Applied when a token is taken and never written back, so admin edits stay and the cap
    returns to its stored value by itself.
    """
    until = getattr(settings, "OFF_PRODUCT_BOOST_UNTIL", None)
    if key == "off-product" and stored is not None and until is not None and _utc_day(now) <= until:
        return max(stored, settings.OFF_PRODUCT_BOOST_DAILY)
    return stored


def _new_day(b: Bucket, now: datetime) -> Bucket:
    today = _utc_day(now)
    if b.day != today:
        return replace(b, day=today, daily_used=0)
    return b


def _refusal(b: Bucket, now: datetime) -> float | None:
    """Seconds to wait while blocked or at the daily cap; None when neither applies."""
    if b.blocked_until is not None and now < b.blocked_until:
        return (b.blocked_until - now).total_seconds()
    if b.daily_limit is not None and b.daily_used >= b.daily_limit:
        midnight = datetime.combine(
            _utc_day(now) + timedelta(days=1), datetime.min.time(), UTC
        )
        return (midnight - now).total_seconds()
    return None


def take(b: Bucket, now: datetime) -> tuple[Bucket, float]:
    """Take one token: ``(new_bucket, 0.0)`` on grant, ``(refilled_bucket, wait)`` on refusal."""
    b = _new_day(b, now)
    wait = _refusal(b, now)
    if wait is not None:
        return b, wait
    elapsed = max(
        0.0, (now - b.refilled_at).total_seconds()
    )  # a clock behind never drains
    tokens = min(b.capacity, b.tokens + elapsed * b.refill_per_second)
    b = replace(b, tokens=tokens, refilled_at=max(b.refilled_at, now))
    if tokens >= 1 - EPSILON:
        return replace(b, tokens=max(0.0, tokens - 1), daily_used=b.daily_used + 1), 0.0
    if b.refill_per_second <= 0:
        return b, NO_REFILL_WAIT
    return b, (1 - tokens) / b.refill_per_second


def backoff(
    b: Bucket, now: datetime, status: int | None, retry_after: float | None
) -> Bucket:
    if status is None or status in (429, 503):
        exponent = min(b.consecutive_failures, BACKOFF_MAX_EXPONENT)
        delay = max(min(BACKOFF_BASE * 2**exponent, BACKOFF_MAX), retry_after or 0)
        return replace(
            b,
            blocked_until=now + timedelta(seconds=delay),
            consecutive_failures=min(b.consecutive_failures + 1, MAX_FAILURES),
        )
    if 200 <= status < 300:
        # One success eases the backoff by one step instead of forgetting it, so a run that
        # alternates 429s and 200s keeps slowing down. A block still in force is kept.
        blocked = b.blocked_until
        if blocked is not None and blocked <= now:
            blocked = None
        return replace(
            b, blocked_until=blocked, consecutive_failures=max(0, b.consecutive_failures - 1)
        )
    return b


def refund(b: Bucket, status: int | None) -> Bucket:
    """Give back the daily count of a request that failed (transport error, 429 or 5xx).

    Such a request answered nothing useful, so it should not use up the day's cap. Clamped
    at 0, so a refund for a request granted on an earlier UTC day never goes negative.
    """
    if status is None or status == 429 or 500 <= status < 600:
        return replace(b, daily_used=max(0, b.daily_used - 1))
    return b


_FIELDS = tuple(Bucket.__dataclass_fields__)


def _bucket(row: SourceBudget) -> Bucket:
    return Bucket(**{name: getattr(row, name) for name in _FIELDS})


class DbBudget:
    """The ``Budget`` every live source and scheduled run draws from.

    One instance may be shared by threads: each thread uses its own DB connection.
    """

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = timezone.now,
        sleep: Callable[[float], object] = time.sleep,
        stop: threading.Event | None = None,
    ) -> None:
        self._now = now
        self._sleep = sleep
        self._stop = stop

    @property
    def stopping(self) -> bool:
        """True once the worker was asked to stop; callers should make no new requests."""
        return self._stop is not None and self._stop.is_set()

    def _apply(self, key: str, change: Callable[[Bucket], tuple[Bucket, object]]):
        with transaction.atomic():
            row = SourceBudget.objects.select_for_update().filter(pk=key).first()
            if row is None:
                raise KeyError(key)
            before = _bucket(row)
            after, outcome = change(before)
            if after != before:
                changed = [
                    name
                    for name in _FIELDS
                    if getattr(after, name) != getattr(before, name)
                ]
                for name in changed:
                    setattr(row, name, getattr(after, name))
                row.save(update_fields=changed)
        return outcome

    def acquire(self, key: str) -> None:
        if self.stopping:
            raise Stopping()
        now = self._now()

        def change(b: Bucket):
            boosted = replace(b, daily_limit=boosted_limit(key, b.daily_limit, now))
            after, wait = take(boosted, now)
            return replace(after, daily_limit=b.daily_limit), wait

        wait = self._apply(key, change)
        if wait > 0:
            raise BudgetExhausted(wait)

    def report(
        self, key: str, status: int | None, retry_after: float | None = None
    ) -> None:
        now = self._now()
        self._apply(
            key,
            lambda b: (refund(backoff(b, now, status, retry_after), status), None),
        )

    def wait(
        self,
        key: str,
        *,
        max_wait: float = 60.0,
        sleep: Callable[[float], object] | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        """Block until a token is taken; ``BudgetExhausted`` once ``max_wait`` would be passed.

        A set ``stop`` event raises ``Stopping`` at once, so a shutting-down worker makes no
        more requests.
        """
        sleep = sleep or self._sleep
        stop = stop or self._stop
        waited = 0.0
        while True:
            if stop is not None and stop.is_set():
                raise Stopping()
            try:
                self.acquire(key)
                return
            except Stopping:
                raise
            except BudgetExhausted as exc:
                if waited + exc.wait > max_wait:
                    raise
                if stop is not None:
                    if stop.wait(exc.wait):
                        raise Stopping() from None
                else:
                    sleep(exc.wait)
                waited += exc.wait

    def blocked(self, key: str, now: datetime) -> bool:
        """Blocked by backoff or at the daily cap (a plain read; no lock, no token).

        A missing row is not blocked: rows are seeded when the worker starts.
        """
        row = SourceBudget.objects.filter(pk=key).first()
        if row is None:
            return False
        bucket = _bucket(row)
        bucket = replace(bucket, daily_limit=boosted_limit(key, bucket.daily_limit, now))
        return _refusal(_new_day(bucket, now), now) is not None


def seed_budgets() -> None:
    """Create missing rows from ``settings.SOURCE_BUDGETS``; existing rows (admin edits) are kept."""
    now = timezone.now()
    for key, cfg in settings.SOURCE_BUDGETS.items():
        SourceBudget.objects.get_or_create(
            source=key,
            defaults={
                "capacity": cfg["capacity"],
                "refill_per_second": cfg["refill_per_second"],
                "tokens": cfg["capacity"],
                "refilled_at": now,
                "daily_limit": cfg["daily_limit"],
                "daily_used": 0,
                "day": _utc_day(now),
            },
        )
