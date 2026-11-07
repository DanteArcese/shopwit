import logging
import signal
import threading
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
