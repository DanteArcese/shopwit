"""OFF product lookups for observed offers' barcodes (#88). No network: recorded responses."""

import io
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command

from catalog import observed_off, scheduler
from catalog.models import IngestionRun, OffEntry, Offer
from catalog.observed_off import CONNECTOR, backfill_sizes, candidates
from connectors.live import BudgetExhausted, Stopping
from connectors.recording import recorded_transport
from enrichment.off import USER_AGENT
from tests.catalog.test_observed import NOW, loc, obs, sync

RECORDINGS = (
    Path(__file__).resolve().parents[1] / "enrichment" / "recordings" / "off_product"
)
FAIRLIFE = "0811620020169"  # OFF: "52 fl oz", whole milk
EGGS = "0078742148380"  # OFF: quantity "12" (no unit)
NO_SIZE = "0093966010015"  # OFF: found, no quantity
MISSING = "0068826718115"  # OFF: 404


@pytest.fixture(autouse=True)
def cats(db, settings):
    from catalog.categories_sync import sync_categories

    sync_categories(settings.CATEGORIES_DIR)


class FakeBudget:
    def __init__(self, tokens=None):
        self.tokens = tokens
        self.waits = []
        self.reports = []

    def wait(self, key):
        if self.tokens is not None:
            if self.tokens == 0:
                raise BudgetExhausted(3600.0)
            self.tokens -= 1
        self.waits.append(key)

    def report(self, key, status, retry_after=None):
        self.reports.append((key, status))


def recorded(seen=None):
    transport = recorded_transport(RECORDINGS, strict=True)

    def handler(request):
        if seen is not None:
            seen.append(request)
        return transport.handle_request(request)

    return httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )


def observe(*specs):
    """Observed offers without a size, as Open Prices often sends them."""
    sync(
        *[
            obs(
                price_id=i,
                upc=upc,
                title=title,
                brands=brand,
                quantity=None,
                labels=frozenset(),
                location=loc(osm_id=i),
            )
            for i, (upc, title, brand) in enumerate(specs, start=1)
        ]
    )


def offer(upc):
    return Offer.objects.get(retailer_item_id__startswith=f"{upc}@")


def test_fills_size_from_off_and_renormalizes():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    before = offer(FAIRLIFE)
    assert "quantity" in before.unresolved and before.unit_prices == {}

    seen = []
    budget = FakeBudget()
    run, stats = backfill_sizes(NOW, budget, client=recorded(seen))

    assert (run.connector, run.status, run.error) == (CONNECTOR, "succeeded", "")
    assert (stats.requests, stats.stored, stats.renormalized, stats.matched) == (
        1,
        1,
        1,
        1,
    )
    entry = OffEntry.objects.get(pk=FAIRLIFE)
    assert entry.quantity == "52 fl oz" and "en:no-lactose" in entry.labels
    after = offer(FAIRLIFE)
    assert after.category.slug == "dairy/milk"
    assert after.measures["volume_ml"] == pytest.approx(1537.82, rel=1e-3)
    assert "quantity" not in after.unresolved and "per_volume" in after.unit_prices
    assert after.labels == sorted(entry.labels)
    assert after.category_path is not None  # from OFF categories
    assert after.measure_dim == "volume"
    assert after.last_seen_at == before.last_seen_at  # nothing new was observed
    assert after.kind == Offer.Kind.OBSERVED and after.store_id == before.store_id
    assert budget.waits == [CONNECTOR] and budget.reports == [(CONNECTOR, 200)]
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].url.host == "world.openfoodfacts.org"


def test_quantity_without_unit_stays_unresolved():
    observe((EGGS, "EGGS", "EGG-LAND'S BEST"))
    backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert OffEntry.objects.get(pk=EGGS).quantity == "12"
    eggs = offer(EGGS)
    assert eggs.category.slug == "dairy/eggs"
    assert (
        "quantity" in eggs.unresolved
    )  # a bare "12" is not read as 12 eggs: never guess


def test_missing_and_sizeless_are_rechecked_only_after_a_while():
    observe(
        (NO_SIZE, "Organic Whole Milk", "Horizon Organic"),
        (MISSING, "Whole Milk", "Brand"),
    )
    _, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert (stats.requests, stats.stored, stats.not_found) == (2, 1, 1)
    assert OffEntry.objects.get(pk=MISSING).quantity is None
    assert OffEntry.objects.get(pk=NO_SIZE).categories == [
        "en:dairies",
        "en:milks-liquid-and-powder",
        "en:milks",
    ]
    assert "quantity" in offer(NO_SIZE).unresolved

    later = NOW + timedelta(days=1)
    assert candidates(later, 300) == []  # asked recently: not again
    OffEntry.objects.update(
        fetched_at=NOW - observed_off.RECHECK_AFTER - timedelta(days=1)
    )
    assert {upc for upc, _ in candidates(NOW, 300)} == {NO_SIZE, MISSING}


def test_not_found_keeps_an_existing_entry():
    observe((MISSING, "Whole Milk", "Brand"))
    OffEntry.objects.create(
        upc=MISSING, source="off", labels=["en:organic"], quantity=None
    )
    OffEntry.objects.update(fetched_at=NOW - timedelta(days=60))
    backfill_sizes(NOW, FakeBudget(), client=recorded())
    entry = OffEntry.objects.get(pk=MISSING)
    assert entry.labels == ["en:organic"]  # a 404 never wipes cached data
    assert entry.fetched_at == NOW  # only the check time moved


def test_entries_with_a_quantity_are_skipped():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    OffEntry.objects.create(upc=FAIRLIFE, source="off", quantity="52 fl oz")
    assert candidates(NOW, 300) == []


def test_curated_first_newest_next_and_limit():
    sync(
        obs(
            price_id=1,
            upc="0000000000017",
            title="Paprika",
            quantity=None,
            days=1,
            location=loc(osm_id=1),
        ),
        obs(
            price_id=2,
            upc=NO_SIZE,
            title="Organic Whole Milk",
            quantity=None,
            days=9,
            location=loc(osm_id=2),
        ),
        obs(
            price_id=3,
            upc=FAIRLIFE,
            title="Whole ultra-filtered milk",
            quantity=None,
            days=3,
            location=loc(osm_id=3),
        ),
    )
    assert candidates(NOW, 300) == [
        (FAIRLIFE, "off"),
        (NO_SIZE, "off"),
        ("0000000000017", "off"),
    ]
    assert candidates(NOW, 2) == [(FAIRLIFE, "off"), (NO_SIZE, "off")]


def test_household_barcodes_go_to_open_products_facts():
    sync(
        obs(
            price_id=1,
            upc="0037000792468",
            title="Charmin Ultra Soft Toilet Paper",
            quantity=None,
        )
    )
    assert candidates(NOW, 10) == [("0037000792468", "opf")]


def test_failed_lookup_stores_nothing_and_reports_status():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    budget = FakeBudget()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    run, stats = backfill_sizes(NOW, budget, client=client)
    assert run.status == "succeeded" and stats.failed == 1 and run.skipped == 1
    assert not OffEntry.objects.exists()
    assert budget.reports == [(CONNECTOR, 503)]  # the shared row backs off
    assert candidates(NOW, 10) == [(FAIRLIFE, "off")]  # asked again next run


def test_transport_error_is_a_failed_lookup():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))

    def boom(request):
        raise httpx.ConnectError("down")

    budget = FakeBudget()
    _, stats = backfill_sizes(
        NOW, budget, client=httpx.Client(transport=httpx.MockTransport(boom))
    )
    assert stats.failed == 1 and budget.reports == [(CONNECTOR, None)]


def test_budget_exhausted_ends_the_run_but_keeps_what_was_fetched():
    observe(
        (FAIRLIFE, "Whole ultra-filtered milk", "fairlife"),
        (NO_SIZE, "Organic Whole Milk", "Horizon"),
    )
    run, stats = backfill_sizes(NOW, FakeBudget(tokens=1), client=recorded())
    assert (run.status, run.error) == ("succeeded", "budget exhausted")
    assert stats.requests == 1 and OffEntry.objects.count() == 1
    # The first barcode (same day, lower code) was stored and its offer renormalized anyway.
    assert stats.renormalized == 1 and not OffEntry.objects.filter(pk=FAIRLIFE).exists()
    assert offer(NO_SIZE).category_path is not None


def test_stopping_interrupts():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    run, stats = backfill_sizes(
        NOW, FakeBudget(), client=recorded(), stopping=lambda: True
    )
    assert (run.status, run.error) == ("failed", "interrupted") and stats.requests == 0


def test_budget_stopping_is_interrupted():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))

    class Stopped(FakeBudget):
        def wait(self, key):
            raise Stopping()

    run, _ = backfill_sizes(NOW, Stopped(), client=recorded())
    assert (run.status, run.error) == ("failed", "interrupted")


def test_one_bad_offer_does_not_stop_the_rest(monkeypatch):
    observe(
        (FAIRLIFE, "Whole ultra-filtered milk", "fairlife"), (EGGS, "EGGS", "Brand")
    )
    real = observed_off.normalize

    def flaky(raw, *args, **kw):
        if raw.upc == EGGS:
            raise ValueError("bad")
        return real(raw, *args, **kw)

    monkeypatch.setattr(observed_off, "normalize", flaky)
    run, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert run.status == "succeeded" and stats.errors == 1 and stats.renormalized == 1
    assert "per_volume" in offer(FAIRLIFE).unit_prices


def test_nothing_to_do_makes_no_request():
    run, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert run.status == "succeeded" and stats.requests == 0


def test_command_runs_offline_with_recordings(settings):
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    out = io.StringIO()
    call_command("off_observed_sizes", "--recordings", str(RECORDINGS), stdout=out)
    assert "status=succeeded" in out.getvalue() and "stored=1" in out.getvalue()
    assert "per_volume" in offer(FAIRLIFE).unit_prices
    from catalog.models import SourceBudget

    assert (
        SourceBudget.objects.get(pk=CONNECTOR).daily_used == 1
    )  # drew from the shared budget


def test_off_product_budget_default(settings):
    assert settings.SOURCE_BUDGETS[CONNECTOR] == {
        "capacity": 1.0,
        "refill_per_second": 1.0,
        "daily_limit": 300,
    }


# Scheduler


class FakeOP:
    unparsed = 0

    def __init__(self, budget_wait=None, budget_report=None):
        pass

    def fetch_window(self, since, currency):
        return []

    def close(self):
        pass


def test_tick_runs_sizes_after_a_successful_sync(settings, monkeypatch):
    from catalog.budget import DbBudget

    settings.OPEN_PRICES_ENABLED = True
    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)
    calls = []

    def fake(now, budget, **kw):
        calls.append(budget)
        run = IngestionRun.objects.create(
            connector=CONNECTOR, category_slug="all", started_at=now, status="succeeded"
        )
        return run, observed_off.Stats()

    monkeypatch.setattr(scheduler.observed_off, "backfill_sizes", fake)
    budget = DbBudget(now=lambda: NOW, sleep=lambda s: None)
    runs = scheduler.tick(NOW, {}, budget=budget)
    assert [(r.connector, r.status) for r in runs] == [
        ("openprices", "succeeded"),
        (CONNECTOR, "succeeded"),
    ]
    assert calls == [budget]


def test_tick_skips_sizes_without_budget_or_after_failed_sync(settings, monkeypatch):
    settings.OPEN_PRICES_ENABLED = True
    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)
    monkeypatch.setattr(
        scheduler.observed_off,
        "backfill_sizes",
        lambda *a, **k: pytest.fail("must not run"),
    )
    assert [r.connector for r in scheduler.tick(NOW, {})] == ["openprices"]


def test_tick_isolates_a_sizes_crash(settings, monkeypatch):
    from catalog.budget import DbBudget

    settings.OPEN_PRICES_ENABLED = True
    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(scheduler.observed_off, "backfill_sizes", boom)
    runs = scheduler.tick(
        NOW, {}, budget=DbBudget(now=lambda: NOW, sleep=lambda s: None)
    )
    assert [(r.connector, r.status, r.error) for r in runs][-1] == (
        CONNECTOR,
        "failed",
        "RuntimeError",
    )
