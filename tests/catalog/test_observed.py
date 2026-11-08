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


def loc(osm_id=1, name="Safeway", brand="Safeway", city="Mountain View"):
    return Location("NODE", osm_id, name, brand, city, "94040", "US", "OSM")


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
    def __init__(self, observations=(), error=None, unparsed=0):
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
    sync(obs())
    product = Product.objects.get()
    run = sync()
    assert run.status == "succeeded"
    assert OpenPriceObservation.objects.count() == 0
    assert Offer.objects.count() == 0
    assert Store.objects.count() == 0
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


def test_unmatched_title_stored_but_no_offer():
    run = sync(obs(title="Fairlife protein drink", upc="0811620020176"))
    assert OpenPriceObservation.objects.count() == 1
    assert Offer.objects.count() == 0
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
