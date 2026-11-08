import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from django.conf import settings

from catalog import budget as budget_module
from catalog.budget import Bucket, DbBudget, backoff, seed_budgets, take
from catalog.models import SourceBudget
from connectors.live import BudgetExhausted

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def bucket(**kw):
    values = dict(
        capacity=10.0,
        refill_per_second=10 / 60,
        tokens=10.0,
        refilled_at=T0,
        daily_limit=None,
        daily_used=0,
        day=T0.date(),
        blocked_until=None,
        consecutive_failures=0,
    )
    values.update(kw)
    return Bucket(**values)


def test_rate_limiter():
    b = bucket()
    for _ in range(10):
        b, wait = take(b, T0)
        assert wait == 0.0
    refused, wait = take(b, T0)
    assert wait == pytest.approx(6.0)
    b2, wait = take(refused, T0 + timedelta(seconds=6))
    assert wait == 0.0 and b2.tokens == pytest.approx(0.0, abs=1e-6)

    blocked = backoff(bucket(), T0, 429, 120)
    assert blocked.blocked_until == T0 + timedelta(seconds=120)
    assert blocked.consecutive_failures == 1
    assert take(blocked, T0 + timedelta(seconds=10))[1] == pytest.approx(110.0)
    b503 = backoff(bucket(consecutive_failures=3), T0, 503, None)
    assert b503.blocked_until == T0 + timedelta(seconds=40)
    assert b503.consecutive_failures == 4
    ok = backoff(b503, T0, 200, None)
    assert ok.blocked_until is None and ok.consecutive_failures == 0
    assert backoff(bucket(), T0, 404, None) == bucket()
    capped = backoff(bucket(consecutive_failures=12), T0, None, None)
    assert capped.blocked_until == T0 + timedelta(seconds=900)

    late = datetime(2026, 11, 7, 23, 59, 0, tzinfo=UTC)
    d = bucket(daily_limit=2, refilled_at=late)
    d, w1 = take(d, late)
    d, w2 = take(d, late)
    refused, w3 = take(d, late)
    assert (w1, w2) == (0.0, 0.0)
    assert w3 == pytest.approx(60.0)
    after, w4 = take(refused, datetime(2026, 11, 8, 0, 0, 1, tzinfo=UTC))
    assert w4 == 0.0 and after.daily_used == 1 and after.day == date(2026, 11, 8)


def test_take_never_refills_backwards_and_never_waits_forever():
    b = bucket(tokens=0.0, refilled_at=T0)
    earlier, wait = take(
        b, T0 - timedelta(seconds=30)
    )  # another process's clock is behind
    assert earlier.refilled_at == T0 and earlier.tokens == 0.0
    _, wait = take(bucket(tokens=0.0, refill_per_second=0.0), T0)
    assert 0 < wait < float("inf")


def make_row(key="walmart", **kw):
    values = dict(
        capacity=5.0,
        refill_per_second=1.0,
        tokens=5.0,
        refilled_at=datetime.now(UTC),
        daily_limit=None,
        daily_used=0,
        day=datetime.now(UTC).date(),
    )
    values.update(kw)
    return SourceBudget.objects.create(source=key, **values)


@pytest.mark.django_db(transaction=True)
def test_two_threads_one_token(monkeypatch):
    make_row(tokens=1.0, refill_per_second=0.0)
    original_take = budget_module.take

    def slow_take(b, now):  # widen the read-modify-write window so a missing lock shows
        time.sleep(0.3)
        return original_take(b, now)

    monkeypatch.setattr(budget_module, "take", slow_take)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        from django.db import connection

        try:
            barrier.wait()
            DbBudget().acquire("walmart")
            outcomes.append("ok")
        except BudgetExhausted:
            outcomes.append("exhausted")
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(outcomes) == ["exhausted", "ok"]
    assert SourceBudget.objects.get(pk="walmart").tokens == pytest.approx(0.0)


@pytest.mark.django_db
def test_wait_gives_up_after_max_wait():
    make_row(daily_limit=1, daily_used=1)
    slept = []
    with pytest.raises(BudgetExhausted):
        DbBudget().wait("walmart", max_wait=60, sleep=slept.append)
    assert sum(slept) <= 60

    make_row("off-search", tokens=0.0, refill_per_second=0.5)  # 2 s per token
    slept.clear()
    with pytest.raises(BudgetExhausted):
        DbBudget(now=lambda: T0).wait("off-search", max_wait=60, sleep=slept.append)
    assert 0 < sum(slept) <= 60


@pytest.mark.django_db
def test_wait_returns_once_a_token_is_free():
    clock = [T0]
    make_row(tokens=0.0, refill_per_second=1.0, refilled_at=T0)
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    DbBudget(now=lambda: clock[0]).wait("walmart", sleep=sleep)
    assert slept == [pytest.approx(1.0)]


@pytest.mark.django_db
def test_wait_stops_promptly_on_shutdown():
    make_row(tokens=5.0)
    stop = threading.Event()
    stop.set()
    with pytest.raises(BudgetExhausted):
        DbBudget(stop=stop).wait("walmart", sleep=lambda s: pytest.fail("slept"))


@pytest.mark.django_db
def test_report_and_blocked():
    make_row()
    budget = DbBudget(now=lambda: T0)
    now = T0
    assert not budget.blocked("walmart", now)
    budget.report("walmart", 429, 30)
    row = SourceBudget.objects.get(pk="walmart")
    assert (
        row.blocked_until == T0 + timedelta(seconds=30)
        and row.consecutive_failures == 1
    )
    assert budget.blocked("walmart", now)
    assert not budget.blocked("walmart", now + timedelta(seconds=31))
    with pytest.raises(BudgetExhausted) as exc:
        budget.acquire("walmart")
    assert exc.value.wait == pytest.approx(30.0)
    budget.report("walmart", 200)
    assert not budget.blocked("walmart", now)
    SourceBudget.objects.filter(pk="walmart").update(
        daily_limit=3, daily_used=3, day=T0.date()
    )
    assert budget.blocked("walmart", now)
    assert not budget.blocked("walmart", now + timedelta(days=1))  # a new UTC day
    with pytest.raises(KeyError):
        budget.acquire("nope")
    with pytest.raises(KeyError):
        budget.report("nope", 200)


@pytest.mark.django_db
def test_seed_budgets_creates_missing_rows_only():
    seed_budgets()
    assert set(SourceBudget.objects.values_list("source", flat=True)) == set(
        settings.SOURCE_BUDGETS
    )
    row = SourceBudget.objects.get(pk="walmart")
    assert row.tokens == row.capacity == settings.SOURCE_BUDGETS["walmart"]["capacity"]
    assert row.daily_limit == settings.SOURCE_BUDGETS["walmart"]["daily_limit"]
    SourceBudget.objects.filter(pk="walmart").update(capacity=99)  # an admin edit
    seed_budgets()
    assert SourceBudget.objects.get(pk="walmart").capacity == 99


def test_backoff_never_overflows():
    b = backoff(bucket(consecutive_failures=5000), T0, 503, None)
    assert b.blocked_until == T0 + timedelta(seconds=900)
    assert b.consecutive_failures == 1000
    assert backoff(b, T0, None, None).consecutive_failures == 1000
    assert backoff(bucket(consecutive_failures=5000), T0, 429, 3600).blocked_until == T0 + timedelta(hours=1)


@pytest.mark.django_db
def test_acquire_and_wait_raise_stopping_when_stopping():
    from connectors.live import Stopping

    make_row(tokens=5.0)
    stop = threading.Event()
    stop.set()
    budget = DbBudget(stop=stop)
    assert budget.stopping
    with pytest.raises(Stopping):
        budget.acquire("walmart")
    with pytest.raises(Stopping):
        budget.wait("walmart")
    assert SourceBudget.objects.get(pk="walmart").tokens == 5.0  # no token spent
    assert issubclass(Stopping, BudgetExhausted)
