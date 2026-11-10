import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from django.conf import settings

from catalog import budget as budget_module
from catalog.budget import Bucket, DbBudget, backoff, refund, seed_budgets, take
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
    ok = backoff(replace_bucket(b503, blocked_until=T0), T0, 200, None)
    assert ok.blocked_until is None and ok.consecutive_failures == 3
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
    assert budget.blocked("walmart", now)  # a block still in force is kept
    later = DbBudget(now=lambda: now + timedelta(seconds=31))
    later.report("walmart", 200)
    row = SourceBudget.objects.get(pk="walmart")
    assert row.blocked_until is None and row.consecutive_failures == 0
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


def _boost(settings, until=date(2026, 11, 16)):
    settings.OFF_PRODUCT_BOOST_UNTIL = until
    settings.OFF_PRODUCT_BOOST_DAILY = 2000


@pytest.mark.django_db
def test_boost_raises_off_product_cap_without_persisting(settings):
    _boost(settings)
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    make_row("off-product", daily_limit=300, daily_used=300, day=now.date())
    budget = DbBudget(now=lambda: now)
    assert not budget.blocked("off-product", now)
    budget.acquire("off-product")
    row = SourceBudget.objects.get(pk="off-product")
    assert (row.daily_limit, row.daily_used) == (300, 301)


@pytest.mark.django_db
def test_boost_still_stops_at_the_boosted_cap(settings):
    _boost(settings)
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    make_row("off-product", daily_limit=300, daily_used=2000, day=now.date())
    with pytest.raises(BudgetExhausted):
        DbBudget(now=lambda: now).acquire("off-product")


@pytest.mark.django_db
def test_boost_ends_after_until_day(settings):
    _boost(settings)
    now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    make_row("off-product", daily_limit=300, daily_used=300, day=now.date())
    budget = DbBudget(now=lambda: now)
    assert budget.blocked("off-product", now)
    with pytest.raises(BudgetExhausted):
        budget.acquire("off-product")


@pytest.mark.django_db
def test_boost_includes_the_until_day(settings):
    _boost(settings)
    now = datetime(2026, 11, 16, 23, 59, tzinfo=UTC)
    make_row("off-product", daily_limit=300, daily_used=300, day=now.date())
    DbBudget(now=lambda: now).acquire("off-product")


@pytest.mark.django_db
def test_boost_never_touches_other_sources(settings):
    _boost(settings)
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    make_row("walmart", daily_limit=300, daily_used=300, day=now.date())
    with pytest.raises(BudgetExhausted):
        DbBudget(now=lambda: now).acquire("walmart")


def test_boost_keeps_unlimited_unlimited(settings):
    _boost(settings)
    assert budget_module.boosted_limit("off-product", None, T0) is None


def test_no_boost_when_until_is_unset(settings):
    settings.OFF_PRODUCT_BOOST_UNTIL = None
    assert budget_module.boosted_limit("off-product", 300, T0) == 300


@pytest.mark.django_db
def test_boost_end_to_end_through_db_budget(settings):
    settings.OFF_PRODUCT_BOOST_UNTIL = date(2026, 11, 16)
    settings.OFF_PRODUCT_BOOST_DAILY = 400
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    make_row("off-product", capacity=1000.0, tokens=1000.0, daily_limit=300, day=now.date())
    budget = DbBudget(now=lambda: now)
    for _ in range(400):  # past the stored 300, up to the boosted limit
        budget.acquire("off-product")
    with pytest.raises(BudgetExhausted):
        budget.acquire("off-product")
    assert SourceBudget.objects.get(pk="off-product").daily_limit == 300
    after = datetime(2026, 11, 17, 12, tzinfo=UTC)
    later = DbBudget(now=lambda: after)
    for _ in range(300):
        later.acquire("off-product")
    with pytest.raises(BudgetExhausted):
        later.acquire("off-product")


def replace_bucket(b, **kw):
    from dataclasses import replace

    return replace(b, **kw)


def test_success_decrements_failures_and_clears_only_a_passed_block():
    b = bucket(consecutive_failures=2, blocked_until=T0 + timedelta(seconds=30))
    still = backoff(b, T0, 200, None)
    assert still.blocked_until == T0 + timedelta(seconds=30)
    assert still.consecutive_failures == 1
    passed = backoff(b, T0 + timedelta(seconds=31), 200, None)
    assert passed.blocked_until is None and passed.consecutive_failures == 1
    assert backoff(bucket(), T0, 200, None).consecutive_failures == 0


def test_a_200_between_429s_does_not_reset_the_escalation():
    # Two 429s per 200 (the shape of the first boosted run): the delay keeps climbing,
    # where the old full reset bounced 5 s, 10 s, 5 s, 10 s.
    b = bucket()
    now = T0
    delays = []
    for _ in range(3):
        for _ in range(2):
            b = backoff(b, now, 429, None)
            delays.append((b.blocked_until - now).total_seconds())
            now = b.blocked_until + timedelta(seconds=1)
        b = backoff(b, now, 200, None)
    assert delays == [5.0, 10.0, 10.0, 20.0, 20.0, 40.0]


def test_refund_returns_a_count_for_failed_requests_only():
    b = bucket(daily_limit=10, daily_used=5)
    for status in (None, 429, 500, 502, 503, 599):
        assert refund(b, status).daily_used == 4
    for status in (200, 204, 301, 400, 403, 404):
        assert refund(b, status).daily_used == 5
    assert refund(bucket(daily_limit=10, daily_used=0), 429).daily_used == 0


@pytest.mark.django_db
def test_report_refunds_the_daily_count_on_failure():
    make_row("off-product", daily_limit=300, daily_used=10, day=T0.date())
    budget = DbBudget(now=lambda: T0)
    budget.report("off-product", 429)
    row = SourceBudget.objects.get(pk="off-product")
    assert row.daily_used == 9 and row.consecutive_failures == 1
    budget.report("off-product", 404)
    assert SourceBudget.objects.get(pk="off-product").daily_used == 9
    SourceBudget.objects.filter(pk="off-product").update(daily_used=0)
    budget.report("off-product", None)
    assert SourceBudget.objects.get(pk="off-product").daily_used == 0
