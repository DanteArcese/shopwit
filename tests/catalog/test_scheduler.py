import logging
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog import scheduler
from catalog.categories_sync import sync_categories
from catalog.models import Category, IngestionRun, Offer
from connectors.base import SearchResult

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
RECORDINGS = Path(__file__).parent.parent / "connectors" / "recordings" / "walmart"


class FakeConnector:
    name = "fake"
    max_cache_age = timedelta(hours=24)
    records_price_history = True

    def __init__(self):
        self.queries = []

    def search(self, query, limit=25):
        self.queries.append(query)
        return SearchResult(offers=[], skipped=0)


@pytest.fixture
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


def run(connector, slug, status, started):
    return IngestionRun.objects.create(
        connector=connector, category_slug=slug, status=status, started_at=started
    )


def test_due_after_interval_only(cats):
    conns = {"fake": FakeConnector()}
    n_active = Category.objects.filter(active=True).count()
    assert len(scheduler.due(NOW, conns)) == n_active  # never run
    run("fake", "dairy/milk", IngestionRun.Status.SUCCEEDED, NOW - timedelta(hours=11))
    assert ("fake", "dairy/milk") not in scheduler.due(NOW, conns)
    IngestionRun.objects.all().delete()
    run("fake", "dairy/milk", IngestionRun.Status.SUCCEEDED, NOW - timedelta(hours=13))
    assert ("fake", "dairy/milk") in scheduler.due(NOW, conns)


def test_inactive_category_not_due(cats):
    Category.objects.filter(slug="dairy/milk").update(active=False)
    assert ("fake", "dairy/milk") not in scheduler.due(NOW, {"fake": FakeConnector()})


def test_failed_run_does_not_count_as_refreshed(cats):
    run("fake", "dairy/milk", IngestionRun.Status.FAILED, NOW - timedelta(hours=1))
    assert ("fake", "dairy/milk") in scheduler.due(NOW, {"fake": FakeConnector()})


def test_tick_runs_ingest_for_due_pairs(cats):
    conn = FakeConnector()
    Category.objects.exclude(slug="dairy/milk").update(active=False)
    runs = scheduler.tick(NOW, {"fake": conn})
    assert [(r.connector, r.category_slug) for r in runs] == [("fake", "dairy/milk")]
    assert conn.queries == cats.search
    assert runs[0].status == IngestionRun.Status.SUCCEEDED
    assert scheduler.tick(NOW, {"fake": conn}) == []  # now fresh


def test_refresh_command_with_recordings(cats, monkeypatch):
    for var in ("WALMART_CONSUMER_ID", "WALMART_PRIVATE_KEY", "WALMART_KEY_VERSION"):
        monkeypatch.delenv(var, raising=False)
    call_command("refresh", connector="walmart", category="dairy/milk", recordings=str(RECORDINGS))
    ids = set(Offer.objects.filter(retailer="walmart").values_list("retailer_item_id", flat=True))
    assert ids == {"20101", "20102", "20103", "20104", "20105", "20106", "20107"}
    assert IngestionRun.objects.filter(connector="walmart", status="succeeded").exists()


def test_refresh_recordings_defaults_to_walmart(cats):
    call_command("refresh", category="dairy/milk", recordings=str(RECORDINGS))
    assert IngestionRun.objects.filter(connector="walmart", status="succeeded").exists()


def test_refresh_unknown_connector_names_valid_ones(cats):
    with pytest.raises(CommandError, match="walmart"):
        call_command("refresh", connector="nope")


def test_refresh_rejects_recordings_for_other_connector(cats):
    with pytest.raises(CommandError):
        call_command("refresh", connector="other", recordings=str(RECORDINGS))


def test_refresh_without_connectors_names_env_vars(cats, monkeypatch):
    for var in ("WALMART_CONSUMER_ID", "WALMART_PRIVATE_KEY", "WALMART_KEY_VERSION"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(CommandError, match="WALMART_CONSUMER_ID"):
        call_command("refresh")


def test_run_scheduler_once(cats, monkeypatch, caplog):
    Category.objects.exclude(slug="dairy/milk").update(active=False)
    monkeypatch.setattr(scheduler.registry, "available", lambda *a, **k: {"fake": FakeConnector()})
    with caplog.at_level(logging.INFO, logger="catalog.scheduler"):
        call_command("run_scheduler", once=True)
    assert IngestionRun.objects.filter(connector="fake", status="succeeded").count() == 1
    line = [r.getMessage() for r in caplog.records if "fake" in r.getMessage()][0]
    assert "dairy/milk" in line and "fetched=" in line


def test_run_loop_exits_promptly_when_stopped_during_wait(cats):
    Category.objects.update(active=False)
    stop = threading.Event()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        stop.set()

    ticks = []
    scheduler.run_loop(
        poll_seconds=7, wait=wait, now=lambda: ticks.append(1) or NOW, stop=stop, connectors={}
    )
    assert waits == [7] and len(ticks) == 1


def test_default_wait_is_interrupted_by_stop():
    stop = threading.Event()
    stop.set()
    # would block 60s if the default wait were time.sleep
    scheduler.run_loop(poll_seconds=60, once=False, now=lambda: NOW, stop=stop, connectors={})


def test_sigterm_handler_restored_after_command(cats):
    before = signal.getsignal(signal.SIGTERM)
    call_command("run_scheduler", once=True)
    assert signal.getsignal(signal.SIGTERM) is before


def test_sigterm_sets_stop_event_and_restores():
    stop = threading.Event()
    before = signal.getsignal(signal.SIGTERM)
    with scheduler.sigterm_stops(stop):
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    assert signal.getsignal(signal.SIGTERM) is before


def test_command_does_not_change_logger_level(cats):
    level = scheduler.logger.level
    call_command("run_scheduler", once=True)
    assert scheduler.logger.level == level


class Exploding(FakeConnector):
    def search(self, query, limit=25):
        raise RuntimeError("secret-token-123")


def test_tick_isolates_unexpected_errors_per_pair(cats, caplog):
    Category.objects.exclude(slug="dairy/milk").update(active=False)
    good = FakeConnector()
    good.name = "good"
    bad = Exploding()
    bad.name = "bad"
    with caplog.at_level(logging.INFO, logger="catalog.scheduler"):
        runs = scheduler.tick(NOW, {"bad": bad, "good": good})
    by = {r.connector: r for r in runs}
    assert by["bad"].status == IngestionRun.Status.FAILED
    assert by["bad"].error == "RuntimeError"
    assert by["good"].status == IngestionRun.Status.SUCCEEDED
    assert not IngestionRun.objects.filter(status="running").exists()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-token-123" not in text and "RuntimeError" in text


def test_tick_runs_open_prices_when_enabled(cats, settings, monkeypatch):
    calls = []

    class FakeOP:
        unparsed = 0

        def fetch_window(self, since, currency):
            calls.append((since, currency))
            return []

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)
    settings.OPEN_PRICES_ENABLED = False
    scheduler.tick(NOW, {})
    assert calls == []
    settings.OPEN_PRICES_ENABLED = True
    runs = scheduler.tick(NOW, {})
    assert [(r.connector, r.category_slug, r.status) for r in runs] == [
        ("openprices", "all", "succeeded")
    ]
    assert len(calls) == 2 and calls[1] == "closed"
    assert scheduler.tick(NOW, {}) == []  # now fresh


def test_tick_open_prices_error_is_isolated(cats, settings, monkeypatch):
    class Boom:
        def __init__(self):
            raise RuntimeError("secret")

    monkeypatch.setattr(scheduler, "OpenPricesClient", Boom)
    settings.OPEN_PRICES_ENABLED = True
    runs = scheduler.tick(NOW, {})
    assert [(r.connector, r.status, r.error) for r in runs] == [
        ("openprices", "failed", "RuntimeError")
    ]


def test_tick_survives_open_prices_due_error(cats, settings, monkeypatch, caplog):
    def boom(now):
        raise RuntimeError("db gone")

    monkeypatch.setattr(scheduler.observed, "due", boom)
    settings.OPEN_PRICES_ENABLED = True
    assert scheduler.tick(NOW, {}) == []
    assert "open prices due check failed: RuntimeError" in caplog.text


@pytest.fixture
def fresh_cleanup(monkeypatch):
    monkeypatch.setattr(scheduler, "_sessions_cleared_at", None)
    monkeypatch.setattr(scheduler, "_feedback_purged_at", None)


def test_tick_clears_expired_sessions_daily(cats, fresh_cleanup, monkeypatch):
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session
    from django.utils import timezone as dj_timezone

    expired = SessionStore()
    expired["prefs"] = {"postal_code": "94043"}
    expired.create()
    Session.objects.filter(pk=expired.session_key).update(
        expire_date=dj_timezone.now() - timedelta(seconds=1)
    )
    live = SessionStore()
    live["prefs"] = {"postal_code": "10001"}
    live.create()

    scheduler.tick(NOW, {})
    assert list(Session.objects.values_list("session_key", flat=True)) == [live.session_key]

    calls = []
    monkeypatch.setattr(scheduler, "call_command", lambda name: calls.append(name))
    scheduler.tick(NOW + timedelta(hours=23), {})
    assert calls == []
    scheduler.tick(NOW + timedelta(days=1), {})
    assert calls == ["clearsessions"]


def test_clearsessions_failure_never_stops_tick(cats, fresh_cleanup, monkeypatch, caplog):
    def boom(name):
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler, "call_command", boom)
    assert scheduler.tick(NOW, {}) == []
    assert "clearsessions failed: RuntimeError" in caplog.text
    assert scheduler.clear_expired_sessions(NOW + timedelta(hours=1)) is False  # waits a day


def test_tick_purges_feedback_reply_emails_daily(cats, fresh_cleanup):
    from feedback.models import Feedback

    old = Feedback.objects.create(kind="bug", message="x", reply_email="old@example.com")
    Feedback.objects.filter(pk=old.pk).update(created_at=NOW - timedelta(days=91))
    scheduler.tick(NOW, {})
    old.refresh_from_db()
    assert old.reply_email == ""

    again = Feedback.objects.create(kind="bug", message="y", reply_email="again@example.com")
    Feedback.objects.filter(pk=again.pk).update(status="closed")
    scheduler.tick(NOW + timedelta(hours=23), {})  # not yet a day
    again.refresh_from_db()
    assert again.reply_email == "again@example.com"
    scheduler.tick(NOW + timedelta(days=1), {})
    again.refresh_from_db()
    assert again.reply_email == ""


def test_feedback_purge_failure_never_stops_tick(cats, fresh_cleanup, monkeypatch, caplog):
    import feedback.cleanup

    def boom(now):
        raise RuntimeError("db down")

    monkeypatch.setattr(feedback.cleanup, "purge_reply_emails", boom)
    assert scheduler.tick(NOW, {}) == []
    assert "feedback email purge failed: RuntimeError" in caplog.text
    assert scheduler.purge_feedback_emails(NOW + timedelta(hours=1)) is False


class LiveFake:
    name = "walmart"
    budget_keys = ("walmart",)
    max_cache_age = timedelta(hours=24)
    refresh_after = timedelta(hours=12)

    def __init__(self):
        self.calls = []
        self.closed = False

    def live_search(self, phrase, limit, budget):
        from connectors.live import LiveSearchResult

        budget.acquire("walmart")
        self.calls.append(phrase)
        return LiveSearchResult()

    def close(self):
        self.closed = True


@pytest.mark.django_db(transaction=True)
def test_run_loop_runs_fetch_threads_and_stops_them(settings, monkeypatch):
    from catalog.models import FetchJob, SourceBudget

    settings.FETCH_THREADS = 2
    built = []

    def factory(**kwargs):  # each fetch thread builds (and closes) its own sources
        built.append(LiveFake())
        return {"walmart": built[-1]}

    monkeypatch.setattr(scheduler.registry, "live_sources", factory)
    monkeypatch.setattr(scheduler, "live_source_info", lambda **kw: {"walmart": object()})
    job = FetchJob.objects.create(source="walmart", phrase="oat milk", not_before=NOW)
    stop = threading.Event()

    def wait(seconds):  # the main loop's wait: return once the fetch thread finished the job
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if FetchJob.objects.get(pk=job.pk).status in FetchJob.TERMINAL:
                break
            time.sleep(0.05)
        stop.set()

    scheduler.run_loop(poll_seconds=1, wait=wait, stop=stop, connectors={})
    job.refresh_from_db()
    assert job.status == "succeeded"
    assert len(built) == 2 and len({id(s) for s in built}) == 2
    assert sorted(len(s.calls) for s in built) == [0, 1]
    assert all(s.closed for s in built)
    assert not [t for t in threading.enumerate() if t.name.startswith("fetch-")]
    assert set(SourceBudget.objects.values_list("source", flat=True)) == set(settings.SOURCE_BUDGETS)
    assert SourceBudget.objects.get(pk="walmart").daily_used == 1


def test_once_and_fetch_false_start_no_fetch_threads(cats, monkeypatch):
    def boom(**kwargs):
        raise AssertionError("no fetch threads expected")

    monkeypatch.setattr(scheduler.registry, "live_sources", boom)
    scheduler.run_loop(once=True, connectors={})
    stop = threading.Event()
    scheduler.run_loop(wait=lambda s: stop.set(), stop=stop, connectors={}, fetch=False)


def test_no_fetch_threads_without_live_sources(cats, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise AssertionError("no fetch threads expected")

    monkeypatch.setattr(scheduler, "live_source_info", lambda **kw: {})
    monkeypatch.setattr(scheduler, "start_fetch_threads", boom)
    stop = threading.Event()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            stop.set()

    with caplog.at_level("INFO", logger="catalog.scheduler"):
        scheduler.run_loop(wait=wait, stop=stop, connectors={})
    assert caplog.text.count("no live search sources; fetch threads not started") == 1


def test_tick_runs_fetch_housekeeping(cats):
    from catalog.models import FetchJob

    Category.objects.update(active=False)
    job = FetchJob.objects.create(source="walmart", phrase="oat milk", not_before=NOW)
    FetchJob.objects.filter(pk=job.pk).update(created_at=NOW - timedelta(minutes=3))
    scheduler.tick(NOW, {})
    assert FetchJob.objects.get(pk=job.pk).status == "expired"


def test_tick_housekeeping_error_is_isolated(cats, monkeypatch, caplog):
    Category.objects.update(active=False)

    def boom(now):
        raise RuntimeError("x")

    monkeypatch.setattr(scheduler, "housekeeping", boom)
    with caplog.at_level(logging.ERROR, logger="catalog.scheduler"):
        assert scheduler.tick(NOW, {}) == []
    assert "fetch housekeeping failed: RuntimeError" in caplog.text


def test_open_prices_sync_draws_from_budget(cats, settings, monkeypatch):
    from catalog.budget import DbBudget

    seen = {}

    class FakeOP:
        unparsed = 0

        def __init__(self, budget_wait=None, budget_report=None):
            seen["budget_wait"] = budget_wait
            seen["budget_report"] = budget_report

        def fetch_window(self, since, currency):
            seen["budget_wait"]("openprices")
            return []

        def close(self):
            pass

    settings.OPEN_PRICES_ENABLED = True
    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)
    budget = DbBudget(now=lambda: NOW, sleep=lambda s: None)
    from catalog.models import SourceBudget

    SourceBudget.objects.create(
        source="openprices", capacity=1, refill_per_second=0, tokens=0,
        refilled_at=NOW, day=NOW.date(),
    )
    runs = scheduler.tick(NOW, {}, budget=budget)
    assert [(r.connector, r.status, r.error) for r in runs] == [("openprices", "failed", "budget exhausted")]
    assert seen["budget_report"] == budget.report


def test_tick_stops_starting_work_once_stopping(cats):
    from catalog.budget import DbBudget

    stop = threading.Event()
    stop.set()
    conn = FakeConnector()
    assert scheduler.tick(NOW, {"fake": conn}, budget=DbBudget(stop=stop)) == []
    assert conn.queries == [] and IngestionRun.objects.count() == 0



def test_fetch_join_deadline_counts_from_stop():
    assert scheduler.FETCH_JOIN_TIMEOUT <= 20
    stop = scheduler.StopEvent()
    assert stop.set_at is None
    stop.set()
    assert stop.set_at is not None
    stop.set_at -= scheduler.FETCH_JOIN_TIMEOUT - 0.2  # SIGTERM came almost 20 s ago
    hang = threading.Event()
    thread = threading.Thread(target=hang.wait, args=(10,), name="fetch-test", daemon=True)
    thread.start()
    started = time.monotonic()
    scheduler.stop_fetch_threads([thread], stop)
    assert time.monotonic() - started < 1.5
    hang.set()
    thread.join()


def test_fetch_thread_builds_and_closes_its_own_sources(db, monkeypatch):
    built = []

    def factory(**kwargs):
        built.append(LiveFake())
        return {"walmart": built[-1]}

    monkeypatch.setattr(scheduler.registry, "live_sources", factory)
    stop = threading.Event()
    stop.set()
    scheduler.fetch_thread(stop, scheduler.DbBudget(stop=stop))
    assert len(built) == 1 and built[0].closed


def test_command_stop_event_records_sigterm_time():
    stop = scheduler.StopEvent()
    with scheduler.sigterm_stops(stop):
        signal.raise_signal(signal.SIGTERM)
    assert stop.is_set() and stop.set_at is not None
