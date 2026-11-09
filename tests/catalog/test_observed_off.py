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


# Review round 1


def test_off_product_prefers_entry_fields_and_falls_back_per_field():
    from catalog.observed import observed_off_product
    from normalize.types import OffProduct

    placeholder = OffProduct(labels=frozenset(), quantity=None, categories=())
    got = observed_off_product(placeholder, ["en:organic"], "1 gal", ["en:milks"])
    assert got == OffProduct(frozenset({"en:organic"}), "1 gal", ("en:milks",), None)
    full = OffProduct(frozenset({"en:x"}), "52 fl oz", ("en:dairies",), "ml")
    assert observed_off_product(full, ["en:organic"], "1 gal", ["en:milks"]) == full
    assert observed_off_product(None, [], None, []) == OffProduct(frozenset(), None, (), None)


def test_sync_keeps_open_prices_label_over_a_404_placeholder():
    import enrichment.off
    from catalog.observed import sync_open_prices
    from tests.catalog.test_observed import FakeClient

    OffEntry.objects.create(upc=MISSING, source="off", labels=[], quantity=None)
    sync_open_prices(
        FakeClient([obs(upc=MISSING, title="Organic Whole Milk", quantity="1 gal")]),
        NOW,
        off_lookup=enrichment.off.lookup,
    )
    milk = offer(MISSING)
    assert milk.attributes["organic"]["value"] is True
    assert "en:organic" in milk.labels and milk.measures["volume_ml"] > 3000


def test_renormalize_keeps_open_prices_label_over_a_404_placeholder():
    sync(obs(upc=MISSING, title="Organic Whole Milk", quantity=None))
    backfill_sizes(NOW, FakeBudget(), client=recorded())  # 404: placeholder stored
    stats = observed_off.Stats()
    observed_off.renormalize({MISSING}, NOW, stats)
    milk = offer(MISSING)
    assert stats.renormalized == 1 and "en:organic" in milk.labels


@pytest.mark.parametrize("response", [
    httpx.Response(400), httpx.Response(403), httpx.Response(200, content=b"not json"),
])
def test_non_retryable_failure_is_recorded_so_it_is_not_asked_daily(response):
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: response))
    _, stats = backfill_sizes(NOW, FakeBudget(), client=client)
    assert stats.rejected == 1 and stats.failed == 0
    assert OffEntry.objects.get(pk=FAIRLIFE).quantity is None
    assert candidates(NOW, 10) == []


def test_stopping_during_renormalize_still_merges_written_products(monkeypatch):
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"), (NO_SIZE, "Organic Whole Milk", "H"))
    merged = []
    monkeypatch.setattr(observed_off, "merge_product", lambda p, now: merged.append(p.pk))
    calls = iter([False, True])
    with pytest.raises(Stopping):
        observed_off.renormalize({FAIRLIFE, NO_SIZE}, NOW, observed_off.Stats(), stopping=lambda: next(calls))
    assert len(merged) == 1


def test_renormalize_filters_offers_by_barcode_in_the_database():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    observe(*[(f"00000000000{i:02d}", "Paprika", "B") for i in range(10, 30)])
    stats = observed_off.Stats()
    with CaptureQueriesContext(connection) as ctx:
        observed_off.renormalize({"0000000000010"}, NOW, stats)
    assert stats.renormalized == 1
    offer_selects = [
        q["sql"] for q in ctx.captured_queries
        if q["sql"].startswith('SELECT') and 'FROM "catalog_offer"' in q["sql"]
        and '"catalog_offer"."kind" = ' in q["sql"]
    ]
    assert offer_selects and all("0000000000010@" in sql for sql in offer_selects)


# --- nutrition backfill (#95) ---

from datetime import date, datetime, timezone  # noqa: E402

from catalog.models import Product  # noqa: E402
from catalog.observed_off import nutrition_candidates, run_limit  # noqa: E402


OLDER = datetime(2026, 1, 1, tzinfo=timezone.utc)  # before the recordings' last_modified


def stamp(upc, *, source="off", checked=False, last_modified=True):
    return OffEntry.objects.create(
        upc=upc,
        source=source,
        labels=[],
        quantity="1 gal",  # has a size, so it is not a size candidate
        fetched_at=NOW,
        last_modified=NOW if last_modified else None,
        nutrition_checked_at=NOW if checked else None,
    )


def test_nutrition_candidates_skip_checked_opf_and_placeholders():
    stamp("0000000000001")
    stamp("0000000000002", checked=True)
    stamp("0000000000003", source="opf")
    stamp("0000000000004", last_modified=False)  # 404 placeholder
    stamp("0000000000005")
    assert nutrition_candidates(10, set()) == [
        ("0000000000001", "off"),
        ("0000000000005", "off"),
    ]
    assert nutrition_candidates(10, {"0000000000001"}) == [("0000000000005", "off")]
    assert nutrition_candidates(1, set()) == [("0000000000001", "off")]
    assert nutrition_candidates(0, set()) == []


def test_sizes_first_then_nutrition_within_limit():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"), (EGGS, "EGGS", "E"))
    for i in range(1, 6):
        stamp(f"00000000000{i:02d}")
    seen = []
    _, stats = backfill_sizes(NOW, FakeBudget(), limit=4, client=bad_recorded(seen))
    asked = [r.url.path.rsplit("/", 1)[-1] for r in seen]
    assert asked[:2] == sorted([FAIRLIFE, EGGS], key=asked.index)
    assert set(asked[:2]) == {FAIRLIFE, EGGS}
    assert asked[2:] == ["0000000000001", "0000000000002"]
    assert stats.nutrition == 2 and stats.requests == 4


def bad_recorded(seen):
    """Recordings for the known barcodes, 404 for anything else."""
    transport = recorded_transport(RECORDINGS, strict=True)

    def handler(request):
        seen.append(request)
        if request.url.path.rsplit("/", 1)[-1] in (FAIRLIFE, EGGS):
            return transport.handle_request(request)
        return httpx.Response(404, json={"status": 0, "status_verbose": "product not found"})

    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})


def _walmart_offer(upc):
    product, _ = Product.objects.get_or_create(
        identity=f"p-{upc}", defaults=dict(title="Whole milk 1 gal", upc=upc)
    )
    from decimal import Decimal

    return Offer.objects.create(
        product=product, retailer="walmart", retailer_item_id="w1", price=Decimal("3.00"),
        currency="USD", url="https://example.com/x", title="Whole milk 1 gal",
        last_seen_at=NOW, kind=Offer.Kind.LIVE, measure_dim="mass", measure_amount=1000.0,
    )


def test_backfill_stores_nutrition_and_refreshes_offers():
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", fetched_at=NOW,
        last_modified=OLDER,
    )
    live = _walmart_offer(NO_SIZE)
    assert live.nutrition == {}
    _, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    entry = OffEntry.objects.get(pk=NO_SIZE)
    assert entry.nutrition_checked_at is not None and entry.protein_100g == 3.4
    live.refresh_from_db()
    assert live.nutrition["protein_cost"] is not None
    assert stats.failed == 0


def test_backfill_refreshes_offers_from_a_nutrition_only_candidate():
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=OLDER, fetched_at=NOW
    )
    live = _walmart_offer(NO_SIZE)
    _, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert stats.nutrition == 1 and stats.requests == 1
    live.refresh_from_db()
    assert live.nutrition["protein_cost"] is not None
    assert "nutrition=1" in stats.line()


@pytest.mark.parametrize("status", [429, 503])
def test_backfill_retryable_failure_leaves_row_unchecked(status):
    stamp("0000000000001")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    _, stats = backfill_sizes(NOW, FakeBudget(), client=client)
    assert stats.failed == 1 and stats.stored == 0
    assert OffEntry.objects.get(pk="0000000000001").nutrition_checked_at is None
    assert nutrition_candidates(10, set()) == [("0000000000001", "off")]


def test_backfill_404_marks_existing_entry_checked_and_keeps_data():
    OffEntry.objects.create(
        upc=MISSING, source="off", labels=["en:organic"], quantity="1 gal",
        last_modified=NOW, fetched_at=NOW - timedelta(days=1),
    )
    _, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    entry = OffEntry.objects.get(pk=MISSING)
    assert stats.not_found == 1
    assert entry.nutrition_checked_at is not None
    assert entry.labels == ["en:organic"] and entry.quantity == "1 gal"
    assert nutrition_candidates(10, set()) == []


def test_backfill_404_placeholder_is_marked_checked_and_not_refetched():
    observe((MISSING, "Organic Whole Milk", "H"))
    seen = []
    backfill_sizes(NOW, FakeBudget(), client=recorded(seen))
    placeholder = OffEntry.objects.get(pk=MISSING)
    assert placeholder.last_modified is None and placeholder.nutrition_checked_at is not None
    assert nutrition_candidates(10, set()) == []
    seen.clear()
    later = NOW + timedelta(hours=1)
    backfill_sizes(later, FakeBudget(), client=recorded(seen))
    assert seen == []  # neither a size nor a nutrition candidate


def test_non_retryable_answer_marks_checked():
    observe((FAIRLIFE, "Whole ultra-filtered milk", "fairlife"))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    backfill_sizes(NOW, FakeBudget(), client=client)
    assert OffEntry.objects.get(pk=FAIRLIFE).nutrition_checked_at is not None


def test_run_limit_boost(settings):
    settings.OFF_PRODUCT_BOOST_UNTIL = date(2026, 11, 16)
    settings.OFF_PRODUCT_BOOST_DAILY = 2000
    assert run_limit(datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc)) == 2000
    assert run_limit(datetime(2026, 11, 17, 0, 1, tzinfo=timezone.utc)) == 300
    settings.OFF_PRODUCT_BOOST_UNTIL = None
    assert run_limit(NOW) == 300


# --- fix round 1 ---


def test_stopping_mid_loop_still_refreshes_offers_of_processed_upcs():
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=OLDER, fetched_at=NOW
    )
    OffEntry.objects.create(  # sorts after NO_SIZE, so it is never reached
        upc=FAIRLIFE, source="off", labels=[], quantity="1 L", last_modified=OLDER, fetched_at=NOW
    )
    live = _walmart_offer(NO_SIZE)
    calls = iter([False, True])
    run, _ = backfill_sizes(NOW, FakeBudget(), client=recorded(), stopping=lambda: next(calls))
    assert run.error == "interrupted"
    live.refresh_from_db()
    assert live.nutrition.get("protein_cost") is not None


def test_unexpected_error_mid_loop_still_refreshes_and_finishes_run(monkeypatch):
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=OLDER, fetched_at=NOW
    )
    OffEntry.objects.create(
        upc=FAIRLIFE, source="off", labels=[], quantity="1 L", last_modified=OLDER, fetched_at=NOW
    )
    live = _walmart_offer(NO_SIZE)
    real = observed_off._store
    seen = []

    def store(upc, *a, **k):
        if seen:
            raise RuntimeError("boom")
        seen.append(upc)
        real(upc, *a, **k)

    monkeypatch.setattr(observed_off, "_store", store)
    run, _ = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert run.status == "failed" and run.error == "RuntimeError"
    live.refresh_from_db()
    assert live.nutrition.get("protein_cost") is not None


def test_cached_row_newer_is_checked_and_its_offers_refreshed():
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=NOW, fetched_at=NOW,
        nutrition_per="100g", protein_100g=10.0, kcal_100g=100.0,
    )
    live = _walmart_offer(NO_SIZE)
    OffEntry.objects.filter(pk=NO_SIZE).update(nutrition_checked_at=None)
    _, stats = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert NO_SIZE in stats.changed
    assert OffEntry.objects.get(pk=NO_SIZE).nutrition_checked_at is not None
    live.refresh_from_db()
    assert live.nutrition["protein_cost"] == "3.000000"  # the newer cached values: 100 g protein in a 1,000 g pack at $3.00


# --- final fixes ---


def test_cached_row_newer_without_nutrition_takes_the_fetched_nutrients():
    # a row cached before #95 deployed: newer than the API record, nutrition never stored
    OffEntry.objects.create(
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=NOW, fetched_at=NOW,
    )
    live = _walmart_offer(NO_SIZE)
    backfill_sizes(NOW, FakeBudget(), client=recorded())
    entry = OffEntry.objects.get(pk=NO_SIZE)
    assert entry.nutrition_checked_at is not None
    assert (entry.nutrition_per, entry.protein_100g) == ("100g", 3.4)
    assert entry.last_modified == NOW and entry.quantity == "1 L"  # the newer row's own fields stay
    live.refresh_from_db()
    assert live.nutrition["protein_cost"] is not None


def test_backfill_with_tiny_values_and_bad_packs_does_not_fail():
    OffEntry.objects.create(  # newer than the recording, so its own tiny values are kept
        upc=NO_SIZE, source="off", labels=[], quantity="1 L", last_modified=NOW, fetched_at=NOW,
        nutrition_per="100g", protein_100g=1e-30, kcal_100g=1e-30,
    )
    tiny = _walmart_offer(NO_SIZE)
    nan_pack = Offer.objects.create(
        product=tiny.product, retailer="walmart", retailer_item_id="w2", price=tiny.price,
        currency="USD", url="https://example.com/y", title="Whole milk", last_seen_at=NOW,
        kind=Offer.Kind.LIVE, measure_dim="mass", measure_amount=float("nan"),
    )
    run, _ = backfill_sizes(NOW, FakeBudget(), client=recorded())
    assert run.status == "succeeded"
    tiny.refresh_from_db()
    nan_pack.refresh_from_db()
    assert tiny.nutrition["protein_reason"] == "nutrition values look wrong"
    assert nan_pack.nutrition["protein_reason"] == "pack size unknown"
    assert observed_off.refresh_nutrition([NO_SIZE]) == 0
