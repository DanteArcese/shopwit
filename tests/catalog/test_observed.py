from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.management import call_command

from catalog.categories_sync import sync_categories
from catalog.freshness import fresh_observed, fresh_offers
from catalog.models import (
    IngestionRun,
    Offer,
    OpenPriceObservation,
    PriceHistory,
    Product,
    Store,
)
from catalog.observed import CONNECTOR, due, sync_open_prices
from connectors.base import ConnectorError
from connectors.open_prices import Location, Observation
from tests.catalog.helpers import raw

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
UPC = "0811620020169"
TITLE = "Horizon Organic Whole Milk"


def loc(osm_id=1, name="Safeway", brand="Safeway", city="Mountain View", lat=37.4, lon=-122.1):
    return Location("NODE", osm_id, name, brand, city, "94040", "US", "OSM", lat, lon)


def obs(price_id=1, price="4.99", days=5, location=None, title=TITLE, upc=UPC, **kw):
    fields = dict(
        price_id=price_id,
        type="PRODUCT",
        product_code=upc,
        product_name=title,
        brands="Horizon",
        quantity="1 gal",
        labels=frozenset({"en:organic"}),
        price=Decimal(price),
        currency="USD",
        is_sale=False,
        date=(NOW - timedelta(days=days)).date(),
        location=location or loc(),
        proof_type="RECEIPT",
        source=None,
        duplicate_of=None,
    )
    fields.update(kw)
    return Observation(**fields)


class FakeClient:
    def __init__(self, observations=(), error=None, unparsed=0, unparsed_ids=()):
        self.unparsed_ids = set(unparsed_ids)
        self.observations = list(observations)
        self.error = error
        self.unparsed = unparsed
        self.calls = []

    def fetch_window(self, since, currency):
        self.calls.append((since, currency))
        if self.error:
            raise self.error
        return list(self.observations)


def no_off(upc):
    return None


def sync(*observations, now=NOW, **kw):
    return sync_open_prices(FakeClient(observations, **kw), now, off_lookup=no_off)


@pytest.fixture(autouse=True)
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)


def test_sync_creates_store_observation_and_observed_offer():
    client = FakeClient([obs()])
    run = sync_open_prices(client, NOW, off_lookup=no_off)
    assert run.status == "succeeded"
    assert run.connector == "openprices" and run.category_slug == "all"
    assert client.calls == [((NOW - settings.OBSERVED_MAX_AGE).date(), "USD")]
    store = Store.objects.get(code="osm-n1")
    assert (store.chain_code, store.chain_name) == ("op-safeway", "Safeway")
    assert store.city == "Mountain View" and store.country_code == "US"
    assert OpenPriceObservation.objects.get().price == Decimal("4.99")
    o = Offer.objects.get()
    assert (o.kind, o.retailer, o.retailer_item_id) == ("observed", "op-safeway", f"{UPC}@osm-n1")
    assert o.store == store
    assert o.observed_on == (NOW - timedelta(days=5)).date()
    assert o.url == "https://prices.openfoodfacts.org/prices/1"
    assert o.unit_prices and o.product.category.slug == "dairy/milk"
    assert (run.fetched, run.matched) == (1, 1)


def test_two_locations_same_chain_are_two_offers():
    sync(obs(1, location=loc(1)), obs(2, location=loc(2)))
    offers = Offer.objects.all()
    assert offers.count() == 2
    assert {o.retailer for o in offers} == {"op-safeway"}
    assert Product.objects.count() == 1


def test_latest_observation_per_store_and_upc_wins():
    older, newer = obs(1, "4.49", days=10), obs(2, "4.99", days=2)
    sync(older, newer)
    assert Offer.objects.get().price == Decimal("4.99")
    assert OpenPriceObservation.objects.count() == 2
    sync(older)
    assert Offer.objects.get().price == Decimal("4.49")
    assert OpenPriceObservation.objects.count() == 1


def test_same_date_tie_goes_to_higher_price_id():
    sync(obs(7, "4.10", days=3), obs(9, "4.20", days=3), obs(8, "4.30", days=3))
    assert Offer.objects.get().price == Decimal("4.20")


def test_removed_upstream_is_deleted_on_success():
    keep, gone = obs(1, location=loc(1)), obs(2, location=loc(2), upc="0811620020176", title=TITLE)
    sync(keep, gone)
    assert Offer.objects.count() == 2
    run = sync(keep)
    assert run.status == "succeeded"
    assert list(OpenPriceObservation.objects.values_list("price_id", flat=True)) == [1]
    assert Offer.objects.count() == 1
    assert list(Store.objects.values_list("code", flat=True)) == ["osm-n1"]
    sync(keep, obs(3, location=loc(3), upc="0811620020176"))
    product = Product.objects.get(upc="0811620020176")
    sync(keep)
    assert product.offers.count() == 0


def test_failed_fetch_deletes_nothing():
    sync(obs())
    run = sync(error=ConnectorError("Open Prices request failed: HTTP 503 on page 40"))
    assert run.status == "failed" and "503" in run.error
    assert run.finished_at is not None
    assert OpenPriceObservation.objects.count() == 1
    assert Offer.objects.count() == 1
    assert Store.objects.count() == 1


def test_unexpected_error_marks_failed_with_type_name_only():
    sync(obs())
    run = sync(error=RuntimeError("secret"))
    assert (run.status, run.error) == ("failed", "RuntimeError")
    assert Offer.objects.count() == 1 and OpenPriceObservation.objects.count() == 1
    assert not IngestionRun.objects.filter(status="running").exists()


def test_unmatched_title_stored_as_generic_offer():
    run = sync(obs(title="Fairlife protein drink", upc="0811620020176"))
    assert OpenPriceObservation.objects.count() == 1
    assert Offer.objects.get().category is None
    assert (run.fetched, run.matched) == (1, 0)


def test_walmart_and_observed_share_product():
    walmart = raw(title=TITLE, item="W1", retailer="walmart", upc=UPC, brand="Horizon")
    from catalog.ingest import ingest
    from catalog.models import Category
    from connectors.base import SearchResult
    from tests.catalog.helpers import FakeConnector

    cat = Category.objects.get(slug="dairy/milk")
    ingest(
        FakeConnector({cat.search[0]: SearchResult(offers=[walmart], skipped=0)}, name="walmart"),
        cat,
        NOW,
        off_lookup=no_off,
    )
    live = Offer.objects.get(retailer="walmart")
    sync(obs())
    live.refresh_from_db()
    assert live.kind == "live" and live.observed_on is None and live.store is None
    observed = Offer.objects.get(kind="observed")
    assert observed.product == live.product
    assert Product.objects.count() == 1


def test_observed_offers_write_no_price_history_and_are_not_live():
    sync(obs())
    assert PriceHistory.objects.count() == 0
    assert not fresh_offers(NOW).exists()
    assert fresh_observed(NOW).count() == 1


def test_skipped_rows_are_counted_and_never_stored():
    bad = [
        obs(1, type="CATEGORY"),
        obs(2, currency="EUR"),
        obs(3, location=Location("NODE", 5, "!!! ***", None, None, None, "US", "OSM")),
    ]
    run = sync(obs(10), *bad, unparsed=2)
    assert run.fetched == 1 and run.skipped == 5
    assert OpenPriceObservation.objects.count() == 1
    assert Store.objects.count() == 1


def test_punctuation_only_location_name_is_skipped_as_no_location():
    from connectors.open_prices import skip_reason

    o = obs(location=Location("NODE", 5, "!!!", None, None, None, "US", "OSM"))
    reason = skip_reason(
        o, country="US", currency="USD", include_assisted=True, today=NOW.date()
    )
    assert reason == "no_location"
    sync(o)
    assert Store.objects.count() == 0


def test_due_backs_off_after_failed_run():
    IngestionRun.objects.create(
        connector=CONNECTOR, category_slug="all", status="failed",
        started_at=NOW - timedelta(minutes=10),
    )
    assert not due(NOW)
    IngestionRun.objects.update(started_at=NOW - timedelta(hours=2))
    assert due(NOW)


def test_empty_pull_with_stored_rows_deletes_nothing(caplog):
    sync(obs())
    with caplog.at_level("WARNING"):
        run = sync()
    assert run.status == "succeeded" and "deletions skipped" in run.error
    assert "suspiciously small window" in caplog.text
    assert OpenPriceObservation.objects.count() == 1
    assert Offer.objects.count() == 1 and Store.objects.count() == 1


def test_pull_under_half_of_stored_deletes_nothing():
    four = [obs(i, location=loc(i), upc=UPC) for i in range(1, 5)]
    sync(*four)
    run = sync(four[0])  # 1 of 4 kept
    assert "deletions skipped" in run.error
    assert OpenPriceObservation.objects.count() == 4 and Offer.objects.count() == 4
    run = sync(*four[:2])  # exactly half: deletions proceed
    assert run.error is None or run.error == ""
    assert OpenPriceObservation.objects.count() == 2 and Offer.objects.count() == 2


def test_exception_in_normalize_keeps_existing_offer(monkeypatch):
    from catalog import observed

    sync(obs())
    monkeypatch.setattr(observed, "normalize", lambda *a, **k: 1 / 0)
    run = sync(obs())
    assert run.status == "succeeded" and run.skipped == 1
    assert Offer.objects.count() == 1


def test_unparsed_ids_are_not_deleted():
    sync(obs(1), obs(2, location=loc(2)))
    run = sync(obs(2, location=loc(2)), unparsed=1, unparsed_ids={1})
    assert run.status == "succeeded"
    assert OpenPriceObservation.objects.filter(price_id=1).exists()
    assert Offer.objects.count() == 2


def test_write_phase_exception_rolls_back(monkeypatch):
    sync(obs(1, "4.99"))
    monkeypatch.setattr(
        OpenPriceObservation.objects, "exclude", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    run = sync(obs(1, "5.49"))
    assert (run.status, run.error) == ("failed", "RuntimeError")
    monkeypatch.undo()
    assert OpenPriceObservation.objects.get().price == Decimal("4.99")
    assert Offer.objects.get().price == Decimal("4.99")


def test_observed_offer_never_changes_live_merge():
    from catalog.ingest import ingest
    from catalog.models import Category, ReviewItem
    from connectors.base import SearchResult
    from tests.catalog.helpers import FakeConnector

    walmart = raw(title="Horizon Organic 2% Reduced Fat Milk, 1 gal", item="W1",
                  retailer="walmart", upc=UPC, brand="Horizon")
    cat = Category.objects.get(slug="dairy/milk")
    ingest(
        FakeConnector({cat.search[0]: SearchResult(offers=[walmart], skipped=0)}, name="walmart"),
        cat, NOW, off_lookup=no_off,
    )
    live = Offer.objects.get(retailer="walmart")
    before = (live.unit_prices, list(live.unresolved))
    product = Product.objects.get()
    title, brand, attrs, measures = product.title, product.brand, product.attributes, product.measures
    items_before = set(ReviewItem.objects.values_list("detail", flat=True))

    sync(obs(title="Some Store Brand Whole Milk", quantity="2 L", brands="Other Co"))
    assert Offer.objects.filter(kind="observed").count() == 1
    product.refresh_from_db()
    live.refresh_from_db()
    assert (product.title, product.brand) == (title, brand)
    assert product.attributes == attrs and product.measures == measures
    assert (live.unit_prices, list(live.unresolved)) == before
    assert set(ReviewItem.objects.values_list("detail", flat=True)) == items_before
    assert not any("conflict" in d for d in ReviewItem.objects.values_list("detail", flat=True))


def test_due_respects_interval():
    assert due(NOW)
    IngestionRun.objects.create(
        connector=CONNECTOR, category_slug="all", status="succeeded",
        started_at=NOW - timedelta(hours=23),
    )
    assert not due(NOW)
    assert due(NOW + timedelta(hours=2))
    IngestionRun.objects.all().update(status="failed")
    assert due(NOW)


def test_command_with_recordings(monkeypatch, capsys):
    from pathlib import Path
    from types import SimpleNamespace

    from catalog.management.commands import open_prices_sync

    # The recordings were captured for the window starting 2026-08-10 (NOW - 90 days).
    monkeypatch.setattr(open_prices_sync, "timezone", SimpleNamespace(now=lambda: NOW))
    rec = Path(__file__).parent.parent / "connectors" / "recordings" / "open_prices"
    call_command("open_prices_sync", recordings=str(rec))
    out = capsys.readouterr().out
    assert out.startswith("status=succeeded fetched=")
    run = IngestionRun.objects.get()
    assert run.status == "succeeded" and run.fetched > 0
    assert OpenPriceObservation.objects.count() == run.fetched
    store = Store.objects.get(code="osm-w35603394")
    assert (store.lat, store.lon) == (37.4031206, -122.079306)


ASSISTED = "https://prices.openfoodfacts.org/experiments/price-tag"


def test_assisted_off_deletes_assisted_rows_without_tripping_guard(settings):
    assisted = [obs(i, location=loc(i), source=ASSISTED) for i in range(1, 4)]
    plain = obs(4, location=loc(4))
    sync(*assisted, plain)
    assert OpenPriceObservation.objects.count() == 4
    settings.OPEN_PRICES_INCLUDE_ASSISTED = False
    run = sync(plain)  # 1 of 4 stored, but only 1 could still be kept
    assert not run.error
    assert list(OpenPriceObservation.objects.values_list("price_id", flat=True)) == [4]
    assert Offer.objects.count() == 1


def test_observations_older_than_window_are_deleted_on_success():
    old = [obs(i, location=loc(i), days=85) for i in range(1, 4)]
    fresh = obs(4, location=loc(4), days=1)
    sync(*old, fresh)
    later = NOW + timedelta(days=10)  # the old three are now outside the window
    run = sync_open_prices(FakeClient([fresh]), later, off_lookup=no_off)
    assert not run.error
    assert list(OpenPriceObservation.objects.values_list("price_id", flat=True)) == [4]


def test_allow_deletions_overrides_small_pull_guard():
    four = [obs(i, location=loc(i)) for i in range(1, 5)]
    sync(*four)
    run = sync_open_prices(FakeClient([four[0]]), NOW, off_lookup=no_off, allow_deletions=True)
    assert not run.error
    assert OpenPriceObservation.objects.count() == 1


def test_store_text_truncated_to_column_length():
    long = "X" * 300
    sync(obs(location=loc(name=long, brand=None, city="C" * 300)))
    store = Store.objects.get()
    for field in ("name", "chain_name", "city"):
        assert len(getattr(store, field)) == Store._meta.get_field(field).max_length


def test_command_refuses_while_recent_run_is_running():
    from django.core.management.base import CommandError
    from django.utils import timezone as djtz

    IngestionRun.objects.create(
        connector="openprices", category_slug="all", started_at=djtz.now() - timedelta(minutes=10)
    )
    with pytest.raises(CommandError, match="--force"):
        call_command("open_prices_sync")


def test_command_ignores_stale_running_row(monkeypatch):
    from django.utils import timezone as djtz

    from catalog.management.commands import open_prices_sync

    IngestionRun.objects.create(
        connector="openprices", category_slug="all", started_at=djtz.now() - timedelta(hours=3)
    )
    seen = {}

    def fake(client, now, **kw):
        seen.update(kw)
        return IngestionRun.objects.create(
            connector="openprices", category_slug="all", started_at=now, status="succeeded"
        )

    monkeypatch.setattr(open_prices_sync, "sync_open_prices", fake)
    monkeypatch.setattr(open_prices_sync, "OpenPricesClient", lambda *a, **k: type("C", (), {"close": lambda s: None})())
    call_command("open_prices_sync", allow_deletions=True)
    assert seen == {"allow_deletions": True}


def test_sync_records_store_coordinates():
    sync(obs())
    store = Store.objects.get(code="osm-n1")
    assert (store.lat, store.lon) == (37.4, -122.1)


def test_sync_clears_coordinates_upstream_dropped():
    sync(obs())
    sync(obs(location=loc(lat=None, lon=None)), now=NOW + timedelta(days=1))
    store = Store.objects.get(code="osm-n1")
    assert (store.lat, store.lon) == (None, None)


def test_observed_sync_keeps_generic():
    run = sync(obs(title="Oatly Oat Drink", quantity="64 fl oz", brands="Oatly", upc="7394376616228"))
    offer = Offer.objects.get()
    assert offer.category is None and offer.kind == "observed"
    assert offer.measure_dim == "volume"
    assert offer.generic_unit_price is not None
    assert run.matched == 0 and run.status == "succeeded"
    assert OpenPriceObservation.objects.count() == 1


def test_observed_categories_feed_path_and_are_stored():
    sync(obs(title="Oatly Oat Drink", upc="7394376616228", categories=("en:plant-based-milks",)))
    assert OpenPriceObservation.objects.get().categories == ["en:plant-based-milks"]
    assert Offer.objects.get().category_path == "Open Food Facts/Plant based milks"


def test_stop_mid_sync_is_interrupted_and_still_due():
    import threading

    from catalog.budget import DbBudget, seed_budgets

    seed_budgets()
    stop = threading.Event()
    budget = DbBudget(stop=stop)

    class StoppingClient(FakeClient):
        def fetch_window(self, since, currency):
            budget.wait("openprices")  # page 1
            stop.set()  # SIGTERM arrives
            budget.wait("openprices")  # page 2
            return []

    run = sync_open_prices(StoppingClient(), NOW, off_lookup=no_off)
    assert (run.status, run.error) == ("failed", "interrupted")
    assert due(NOW + timedelta(minutes=1))  # an interrupted run is no upstream failure


def test_stop_during_write_phase_is_interrupted_and_deletes_nothing():
    """A shutdown between offers ends the write cleanly: run "interrupted", no deletions."""
    sync(obs(1, upc="0811620020169"), obs(2, upc="0070038640219", location=loc(osm_id=2)))
    before = set(Offer.objects.values_list("retailer_item_id", flat=True))
    assert len(before) == 2
    stopped = {"now": False}
    lookups = []

    def off_lookup(upc):  # the first offer's write sets the stop request
        lookups.append(upc)
        stopped["now"] = True
        return None

    client = FakeClient([obs(3, upc="0811620020169", location=loc(osm_id=3)), obs(4, upc="0070038640220", location=loc(osm_id=4))])
    run = sync_open_prices(
        client, NOW + timedelta(days=1), off_lookup=off_lookup, allow_deletions=True,
        stopping=lambda: stopped["now"],
    )
    assert (run.status, run.error) == ("failed", "interrupted")
    assert len(lookups) == 1  # the second offer was never written
    assert before <= set(Offer.objects.values_list("retailer_item_id", flat=True))  # nothing deleted
    assert due(NOW + timedelta(days=1, minutes=1))


def test_scheduler_passes_stop_to_open_prices_sync(monkeypatch):
    import threading

    from catalog import scheduler
    from catalog.budget import DbBudget

    seen = {}

    def fake(client, now, **kw):
        seen.update(kw)
        return IngestionRun.objects.create(
            connector="openprices", category_slug="all", started_at=now, status="succeeded"
        )

    monkeypatch.setattr(scheduler.observed, "sync_open_prices", fake)
    stop = threading.Event()
    scheduler._open_prices(NOW, DbBudget(stop=stop))
    assert not seen["stopping"]()
    stop.set()
    assert seen["stopping"]()
