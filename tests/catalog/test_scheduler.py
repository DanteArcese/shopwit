import logging
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
    assert Offer.objects.filter(retailer="walmart").count() > 0
    assert IngestionRun.objects.filter(connector="walmart", status="succeeded").exists()


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
    sleeps = []
    with caplog.at_level(logging.INFO, logger="catalog.scheduler"):
        call_command("run_scheduler", once=True)
    assert IngestionRun.objects.filter(connector="fake", status="succeeded").count() == 1
    line = [r.getMessage() for r in caplog.records if "fake" in r.getMessage()][0]
    assert "dairy/milk" in line and "fetched=" in line


def test_run_loop_sleeps_and_stops_on_flag(cats):
    Category.objects.update(active=False)
    sleeps = []
    stop = {"n": 0}

    def sleep(s):
        sleeps.append(s)
        stop["n"] += 1

    scheduler.run_loop(
        poll_seconds=7, sleep=sleep, now=lambda: NOW,
        should_stop=lambda: stop["n"] >= 2, connectors={},
    )
    assert sleeps == [7, 7]
