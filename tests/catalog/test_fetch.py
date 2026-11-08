import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.db import connection

from catalog import fetch
from catalog.budget import DbBudget
from catalog.categories_sync import sync_categories
from catalog.fetch import claim, enqueue, fetch_loop, housekeeping, run_job
from catalog.ingest import ingest, ingest_live
from catalog.models import (
    Category,
    FetchJob,
    OffEntry,
    Offer,
    OpenPriceObservation,
    PriceHistory,
    SourceBudget,
)
from connectors.live import BudgetExhausted, LiveSearchResult, SourceInfo
from enrichment.off import OffRecord
from tests.catalog.helpers import LACTAID, FakeConnector, no_off, raw

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
WALMART = SourceInfo("walmart", ("walmart",), timedelta(hours=12))
OFF = SourceInfo("off-prices", ("off-search", "openprices"), timedelta(hours=24))
SOURCES = {"walmart": WALMART}


def budget_row(key, **kw):
    values = dict(
        capacity=5.0,
        refill_per_second=1.0,
        tokens=5.0,
        refilled_at=NOW,
        daily_limit=None,
        daily_used=0,
        day=NOW.date(),
    )
    values.update(kw)
    return SourceBudget.objects.create(source=key, **values)


@pytest.fixture
def budgets(db):
    for key in ("walmart", "off-search", "openprices"):
        budget_row(key)


def job(
    phrase="oat milk",
    source="walmart",
    status="queued",
    origin="search",
    created=NOW,
    **kw,
):
    kw.setdefault("not_before", created)
    j = FetchJob.objects.create(
        source=source, phrase=phrase, status=status, origin=origin, **kw
    )
    FetchJob.objects.filter(pk=j.pk).update(created_at=created)
    j.refresh_from_db()
    return j


class FakeSource:
    budget_keys = ("walmart",)
    max_cache_age = timedelta(hours=24)
    refresh_after = timedelta(hours=12)

    def __init__(self, name="walmart", result=None, error=None, before=None):
        self.name = name
        self.result = result or LiveSearchResult()
        self.error = error
        self.before = before
        self.calls = []
        self.closed = False

    def live_search(self, phrase, limit, budget):
        self.calls.append((phrase, limit))
        if self.before:
            self.before()
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


# --- enqueue ---------------------------------------------------------------


def test_enqueue_creates_and_reuses(budgets):
    jobs = enqueue(
        "oat milk",
        "search",
        NOW,
        {"walmart": WALMART, "off-prices": OFF},
        client_key=None,
    )
    assert sorted(j.source for j in jobs) == ["off-prices", "walmart"]
    assert all(j.status == "queued" and j.not_before == NOW for j in jobs)
    again = enqueue("oat milk", "search", NOW, SOURCES, client_key=None)
    assert [j.pk for j in again] == [j.pk for j in jobs if j.source == "walmart"]
    assert FetchJob.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_enqueue_dedup_concurrent(budgets):
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker():
        try:
            barrier.wait()
            results.append(
                [
                    j.pk
                    for j in enqueue(
                        "oat milk", "search", NOW, SOURCES, client_key=None
                    )
                ]
            )
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    assert FetchJob.objects.filter(status__in=FetchJob.ACTIVE).count() == 1
    only = FetchJob.objects.get().pk
    assert results == [[only], [only]]


def test_refresh_after_skip(budgets):
    job(status="succeeded", finished_at=NOW - timedelta(hours=11))
    assert enqueue("oat milk", "search", NOW, SOURCES, client_key=None) == []
    FetchJob.objects.update(finished_at=NOW - timedelta(hours=13))
    assert len(enqueue("oat milk", "search", NOW, SOURCES, client_key=None)) == 1


def test_personal_phrase_not_enqueued(budgets):
    for phrase in ("555-123-4567", "me@example.com", "4111 1111 1111 1111"):
        assert enqueue(phrase, "search", NOW, SOURCES, client_key=None) == []
    assert enqueue("", "search", NOW, SOURCES, client_key=None) == []
    assert FetchJob.objects.count() == 0


def test_blocked_source_not_enqueued(budgets):
    SourceBudget.objects.filter(pk="openprices").update(
        blocked_until=NOW + timedelta(seconds=30)
    )
    jobs = enqueue(
        "oat milk",
        "search",
        NOW,
        {"walmart": WALMART, "off-prices": OFF},
        client_key=None,
    )
    assert [j.source for j in jobs] == ["walmart"]
    SourceBudget.objects.filter(pk="walmart").update(daily_limit=5, daily_used=5)
    assert enqueue("rice", "search", NOW, SOURCES, client_key=None) == []


def test_queue_limit(budgets):
    FetchJob.objects.bulk_create(
        FetchJob(source="walmart", phrase=f"p{i}", not_before=NOW)
        for i in range(fetch.QUEUE_LIMIT - 1)
    )
    assert (
        len(enqueue("oat milk", "search", NOW, SOURCES, client_key=None)) == 1
    )  # the 200th
    assert enqueue("rice", "search", NOW, SOURCES, client_key=None) == []  # the 201st
    assert FetchJob.objects.count() == fetch.QUEUE_LIMIT


def test_client_throttle(budgets):
    for i in range(fetch.CLIENT_JOBS_PER_MINUTE):
        assert (
            len(enqueue(f"phrase {i}", "search", NOW, SOURCES, client_key="1.2.3.4"))
            == 1
        )
    assert enqueue("one more", "search", NOW, SOURCES, client_key="1.2.3.4") == []
    assert len(enqueue("one more", "search", NOW, SOURCES, client_key="5.6.7.8")) == 1
    assert len(enqueue("warm phrase", "warm", NOW, SOURCES, client_key=None)) == 1
    # Reusing an active job is not a new job and is never throttled.
    assert len(enqueue("phrase 0", "search", NOW, SOURCES, client_key="1.2.3.4")) == 1


# --- claim -----------------------------------------------------------------


def test_visitor_before_warm(budgets):
    job("warm one", origin="warm", created=NOW - timedelta(minutes=5))
    search = job("oat milk", origin="search", created=NOW - timedelta(seconds=5))
    later = job(
        "not yet",
        origin="search",
        created=NOW - timedelta(minutes=9),
        not_before=NOW + timedelta(seconds=1),
    )
    claimed = claim(NOW)
    assert claimed.pk == search.pk
    assert (
        claimed.status == "running"
        and claimed.started_at == NOW
        and claimed.attempts == 1
    )
    assert claim(NOW).origin == "warm"
    assert claim(NOW) is None
    assert FetchJob.objects.get(pk=later.pk).status == "queued"


@pytest.mark.django_db(transaction=True)
def test_skip_locked_two_threads(monkeypatch):
    """The first claimer holds its row lock; the second must take the other job without waiting."""
    a, b = job("a", created=NOW - timedelta(seconds=2)), job("b", created=NOW - timedelta(seconds=1))
    holding, release = threading.Event(), threading.Event()
    original = FetchJob.save
    claimed, errors = {}, []

    def holding_save(self, *args, **kwargs):
        if threading.current_thread().name == "first":
            holding.set()
            release.wait(5)  # keeps the transaction (and the row lock) open
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FetchJob, "save", holding_save)

    def worker():
        try:
            got = claim(NOW)
            claimed[threading.current_thread().name] = got.pk if got else None
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    first = threading.Thread(target=worker, name="first")
    second = threading.Thread(target=worker, name="second")
    first.start()
    assert holding.wait(5)
    second.start()
    second.join(2)
    blocked = second.is_alive()  # without SKIP LOCKED it waits for the first lock
    release.set()
    first.join(5)
    second.join(5)
    assert errors == [] and not blocked
    assert claimed == {"first": a.pk, "second": b.pk}
    assert set(FetchJob.objects.values_list("attempts", flat=True)) == {1}


# --- run_job ---------------------------------------------------------------


def test_budget_wait_requeues(budgets):
    j = job()
    claimed = claim(NOW)
    source = FakeSource(error=BudgetExhausted(5))
    run_job(claimed, {"walmart": source}, DbBudget(), NOW)
    j.refresh_from_db()
    assert j.status == "queued" and j.not_before == NOW + timedelta(seconds=5)
    assert j.attempts == 0 and j.finished_at is None


def test_run_job_success_failure_and_missing_source(budgets):
    sync_categories(settings.CATEGORIES_DIR)
    ok = job("milk")
    result = LiveSearchResult(offers=[raw(retailer="walmart", item="W1")])
    run_job(claim(NOW), {"walmart": FakeSource(result=result)}, DbBudget(), NOW)
    ok.refresh_from_db()
    assert (ok.status, ok.result_count, ok.finished_at, ok.error) == (
        "succeeded",
        1,
        NOW,
        "",
    )

    bad = job("rice")
    run_job(
        claim(NOW),
        {"walmart": FakeSource(error=ValueError("secret text"))},
        DbBudget(),
        NOW,
    )
    bad.refresh_from_db()
    assert (bad.status, bad.error, bad.finished_at) == ("failed", "ValueError", NOW)

    gone = job("beans", source="retired")
    run_job(claim(NOW), {}, DbBudget(), NOW)
    gone.refresh_from_db()
    assert (gone.status, gone.finished_at) == ("skipped", NOW)


def test_run_job_does_not_clobber_a_reclaimed_job(budgets):
    j = job()
    stale = claim(NOW)
    # housekeeping reset it and another worker claimed it again meanwhile
    FetchJob.objects.filter(pk=j.pk).update(
        started_at=NOW + timedelta(minutes=6), attempts=2
    )
    run_job(stale, {"walmart": FakeSource(error=RuntimeError())}, DbBudget(), NOW)
    j.refresh_from_db()
    assert j.status == "running" and j.error == ""


# --- housekeeping ----------------------------------------------------------


def test_expiry_and_stuck_reset(budgets):
    visitor = job("a", origin="search", created=NOW - timedelta(seconds=121))
    listed = job("b", origin="list", created=NOW - timedelta(seconds=121))
    fresh = job("c", origin="search", created=NOW - timedelta(seconds=60))
    warm = job("d", origin="warm", created=NOW - timedelta(seconds=121))
    once = job("e", status="running", started_at=NOW - timedelta(minutes=6), attempts=1)
    twice = job(
        "f", status="running", started_at=NOW - timedelta(minutes=6), attempts=2
    )
    recent = job(
        "g", status="running", started_at=NOW - timedelta(minutes=4), attempts=2
    )
    old = job("h", status="succeeded", finished_at=NOW - timedelta(days=7, seconds=1))
    kept = job("i", status="failed", finished_at=NOW - timedelta(days=6))

    counts = housekeeping(NOW)

    def status(j):
        j.refresh_from_db()
        return j.status

    assert status(visitor) == status(listed) == "expired"
    assert visitor.finished_at == NOW
    assert status(fresh) == status(warm) == "queued"
    assert (
        status(once) == "queued" and once.not_before == NOW and once.started_at is None
    )
    assert (
        status(twice) == "failed"
        and twice.error == "stuck"
        and twice.finished_at == NOW
    )
    assert status(recent) == "running"
    assert not FetchJob.objects.filter(pk=old.pk).exists()
    assert status(kept) == "failed"
    assert counts == {"expired": 2, "requeued": 1, "stuck": 1, "deleted": 1}


# --- fetch loop ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_fetch_loop_survives_job_error(caplog):
    sync_categories(settings.CATEGORIES_DIR)
    first = job("first", source="boom", created=NOW - timedelta(seconds=2))
    second = job("second", source="walmart", created=NOW - timedelta(seconds=1))

    def drop_connection_then_fail():
        connection.close()
        raise RuntimeError("secret detail")

    sources = {
        "boom": FakeSource(name="boom", before=drop_connection_then_fail),
        "walmart": FakeSource(
            result=LiveSearchResult(offers=[raw(retailer="walmart", item="W9")])
        ),
    }
    stop = threading.Event()
    thread = threading.Thread(
        target=fetch_loop,
        args=(stop,),
        kwargs={
            "sources": sources,
            "budget": DbBudget(),
            "poll": 0.05,
            "now": lambda: NOW,
        },
    )
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not FetchJob.objects.filter(status__in=FetchJob.ACTIVE).exists():
            break
        time.sleep(0.05)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, first.error) == ("failed", "RuntimeError")
    assert (second.status, second.result_count) == ("succeeded", 1)
    assert "secret" not in caplog.text and "first" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_fetch_loop_logs_and_continues_after_unexpected_error(monkeypatch, caplog):
    calls = []

    def flaky_claim(now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("db went away")
        return None

    monkeypatch.setattr(fetch, "claim", flaky_claim)
    stop = threading.Event()
    thread = threading.Thread(
        target=fetch_loop,
        args=(stop,),
        kwargs={"sources": {}, "budget": DbBudget(), "poll": 0.01},
    )
    thread.start()
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(5)
    assert not thread.is_alive() and len(calls) >= 3
    assert "fetch job=None source=None failed: RuntimeError" in caplog.text


# --- ingest_live and budgeted scheduled ingest -----------------------------


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


def test_ingest_live_writes_curated_and_generic(milk):
    result = LiveSearchResult(
        offers=[
            raw(LACTAID, retailer="walmart", item="W1"),
            raw(
                "Oatly Oat Milk, 64 fl oz", retailer="walmart", item="W2", brand="Oatly"
            ),
        ]
    )
    assert ingest_live(result, "walmart", NOW, off_lookup=no_off) == 2
    curated = Offer.objects.get(retailer_item_id="W1")
    generic = Offer.objects.get(retailer_item_id="W2")
    assert curated.category == milk and curated.kind == "live"
    assert (
        generic.category is None
        and generic.kind == "live"
        and generic.measure_dim == "volume"
    )
    assert PriceHistory.objects.count() == 0


def test_ingest_live_saves_off_records_and_observations(milk):
    from tests.catalog.test_observed import obs

    record = OffRecord(
        "0811620020169",
        "off",
        frozenset({"en:organic"}),
        "1 gal",
        None,
        ("en:milks",),
        "l",
    )
    stale = obs(price_id=2, currency="EUR")  # fails skip_reason
    result = LiveSearchResult(
        observations=[obs(price_id=1), stale], off_records=[record]
    )
    assert ingest_live(result, "off-prices", NOW) == 1
    assert OffEntry.objects.filter(pk="0811620020169").exists()
    assert list(OpenPriceObservation.objects.values_list("price_id", flat=True)) == [1]
    assert Offer.objects.get().kind == "observed"


def test_ingest_live_skips_one_bad_offer(milk, monkeypatch):
    def lookup(upc):
        if upc == "bad":
            raise ValueError("nope")
        return None

    result = LiveSearchResult(
        offers=[
            raw(retailer="walmart", item="W1", upc="bad"),
            raw(retailer="walmart", item="W2"),
        ]
    )
    assert ingest_live(result, "walmart", NOW, off_lookup=lookup) == 1
    assert list(Offer.objects.values_list("retailer_item_id", flat=True)) == ["W2"]


def test_scheduled_ingest_draws_walmart_budget(milk):
    budget_row("walmart", tokens=0.0, refill_per_second=0.0)
    slept = []
    conn = FakeConnector({}, name="walmart", records_price_history=False)
    run = ingest(
        conn,
        milk,
        NOW,
        off_lookup=no_off,
        budget=DbBudget(now=lambda: NOW, sleep=slept.append),
    )
    assert (run.status, run.error) == ("failed", "budget exhausted")
    assert conn.queries == [] and sum(slept) <= 60


def test_scheduled_ingest_takes_a_token_per_search(milk):
    budget_row("walmart", tokens=5.0, refill_per_second=0.0)
    conn = FakeConnector({}, name="walmart", records_price_history=False)
    run = ingest(conn, milk, NOW, off_lookup=no_off, budget=DbBudget(now=lambda: NOW))
    assert run.status == "succeeded"
    assert SourceBudget.objects.get(pk="walmart").tokens == pytest.approx(
        5.0 - len(milk.search)
    )
    # an unbudgeted connector is never paced
    other = ingest(
        FakeConnector({}, name="fake"), milk, NOW, off_lookup=no_off, budget=DbBudget()
    )
    assert other.status == "succeeded"


# --- fix round 1 -------------------------------------------------------------


def test_heartbeat_keeps_a_slow_job_alive(budgets):
    sync_categories(settings.CATEGORIES_DIR)
    j = job()
    claimed = claim(NOW)
    clock = [NOW]
    seen = {}

    def slow_requests():
        budget = seen["budget"]
        clock[0] = NOW + timedelta(minutes=6)
        budget.acquire("walmart")  # a budgeted request, six minutes in
        seen["housekeeping"] = housekeeping(NOW + timedelta(minutes=10))

    source = FakeSource(result=LiveSearchResult(offers=[raw(retailer="walmart", item="W1")]))
    source.before = slow_requests
    original = source.live_search

    def live_search(phrase, limit, budget):
        seen["budget"] = budget
        return original(phrase, limit, budget)

    source.live_search = live_search
    run_job(claimed, {"walmart": source}, DbBudget(now=lambda: clock[0]), NOW, clock=lambda: clock[0])
    j.refresh_from_db()
    assert seen["housekeeping"]["requeued"] == 0 and seen["housekeeping"]["stuck"] == 0
    assert j.heartbeat_at == NOW + timedelta(minutes=6)
    assert (j.status, j.result_count) == ("succeeded", 1)


def test_reclaimed_job_skips_ingest(budgets):
    sync_categories(settings.CATEGORIES_DIR)
    j = job()
    claimed = claim(NOW)

    def reclaimed():  # housekeeping reset it and another worker claimed it meanwhile
        FetchJob.objects.filter(pk=j.pk).update(started_at=NOW + timedelta(minutes=6))

    source = FakeSource(
        result=LiveSearchResult(offers=[raw(retailer="walmart", item="W1")]), before=reclaimed
    )
    run_job(claimed, {"walmart": source}, DbBudget(), NOW)
    assert Offer.objects.count() == 0
    j.refresh_from_db()
    assert j.status == "running"


def test_housekeeping_uses_heartbeat(budgets):
    alive = job("a", status="running", started_at=NOW - timedelta(minutes=30),
                heartbeat_at=NOW - timedelta(minutes=1), attempts=1)
    dead = job("b", status="running", started_at=NOW - timedelta(minutes=30),
               heartbeat_at=NOW - timedelta(minutes=6), attempts=1)
    housekeeping(NOW)
    alive.refresh_from_db()
    dead.refresh_from_db()
    assert alive.status == "running" and dead.status == "queued"


def test_reused_warm_job_is_promoted_to_visitor(budgets):
    warm = job(origin="warm", created=NOW - timedelta(minutes=10))
    jobs = enqueue("oat milk", "search", NOW, SOURCES, client_key="1.2.3.4")
    assert [j.pk for j in jobs] == [warm.pk]
    warm.refresh_from_db()
    assert warm.origin == "search" and warm.created_at == NOW
    housekeeping(NOW + timedelta(seconds=60))
    warm.refresh_from_db()
    assert warm.status == "queued"  # the visitor TTL counts from the visit
    other = job("rice", origin="search", created=NOW)
    enqueue("rice", "warm", NOW + timedelta(seconds=5), SOURCES, client_key=None)
    other.refresh_from_db()
    assert other.origin == "search" and other.created_at == NOW  # never demoted


def test_stopping_requeues_the_job(budgets):
    stop = threading.Event()
    stop.set()
    j = job()
    claimed = claim(NOW)

    def calls_budget():
        DbBudget(stop=stop).acquire("walmart")

    run_job(claimed, {"walmart": FakeSource(before=calls_budget)}, DbBudget(stop=stop), NOW)
    j.refresh_from_db()
    assert (j.status, j.attempts, j.error) == ("queued", 0, "")


def test_scheduled_ingest_interrupted_on_stop(milk):
    budget_row("walmart")
    stop = threading.Event()
    stop.set()
    conn = FakeConnector({}, name="walmart", records_price_history=False)
    run = ingest(conn, milk, NOW, off_lookup=no_off, budget=DbBudget(stop=stop))
    assert (run.status, run.error) == ("failed", "interrupted")
    assert conn.queries == []
