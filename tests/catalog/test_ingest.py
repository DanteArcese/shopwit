from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.conf import settings

from catalog.categories_sync import sync_categories
from catalog.ingest import ingest
from catalog.identity import product_identity
from catalog.models import Category, IngestionRun, Offer, PriceHistory, Product, ReviewItem
from connectors.base import ConnectorError, SearchResult
from normalize.types import OffProduct, RawOffer

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
LACTAID = "Lactaid Reduced Fat 2% Milk, 2.8 L"


def raw(title=LACTAID, price="6.58", item="A1", retailer="fake", upc=None, brand="Lactaid"):
    return RawOffer(
        retailer=retailer,
        retailer_item_id=item,
        title=title,
        price=Decimal(price),
        currency="USD",
        url=f"https://example.com/{retailer}/{item}",
        fetched_at=NOW,
        upc=upc,
        brand=brand,
    )


class FakeConnector:
    max_cache_age = timedelta(hours=24)

    def __init__(self, results, name="fake", records_price_history=True, error=None):
        self.name = name
        self.records_price_history = records_price_history
        self.results = results
        self.error = error
        self.queries = []

    def search(self, query, limit=25):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results.get(query, SearchResult(offers=[], skipped=0))


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    cat = Category.objects.get(slug="dairy/milk")
    return cat


def all_queries(cat, result):
    return {q: result for q in cat.search}


def no_off(upc):
    return None


@pytest.mark.django_db
def test_ingest_creates_products_offers_and_stats(milk):
    junk = raw(title="Garden Hose 50 ft", item="J1", brand=None)
    unparsed = raw(title="Organic Valley Reduced Fat Organic 2% Milk", item="U1", brand=None)
    result = SearchResult(offers=[raw(), junk, unparsed], skipped=2)
    conn = FakeConnector({milk.search[0]: result})
    run = ingest(conn, milk, NOW, off_lookup=no_off)

    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert (run.fetched, run.matched, run.fully_parsed, run.unresolved, run.skipped) == (3, 2, 1, 1, 2)
    assert conn.queries == list(milk.search)

    p = Product.objects.get(identity="fake:A1")
    assert p.category == milk
    assert p.title == LACTAID
    assert p.brand == "Lactaid"
    assert p.attributes["fat"] == {"value": "2", "source": p.attributes["fat"]["source"]}
    assert p.measures == {"volume_ml": 2800}
    o = Offer.objects.get(retailer="fake", retailer_item_id="A1")
    assert o.product == p
    assert o.price == Decimal("6.58")
    assert o.currency == "USD"
    assert o.unit_prices == {"per_volume": "0.002350"}
    assert o.url == "https://example.com/fake/A1"
    assert o.image_url is None
    assert o.last_seen_at == NOW
    assert Product.objects.count() == 2


@pytest.mark.django_db
def test_reingest_is_idempotent(milk):
    conn = FakeConnector({milk.search[0]: SearchResult([raw(), raw(item="U1", title="Organic Valley Organic 2% Milk", brand=None)], 0)})
    ingest(conn, milk, NOW, off_lookup=no_off)
    snapshot = (
        Product.objects.count(),
        Offer.objects.count(),
        PriceHistory.objects.count(),
        ReviewItem.objects.count(),
    )
    later = NOW + timedelta(hours=12)
    ingest(conn, milk, later, off_lookup=no_off)
    assert snapshot == (
        Product.objects.count(),
        Offer.objects.count(),
        PriceHistory.objects.count(),
        ReviewItem.objects.count(),
    )
    assert Offer.objects.get(retailer_item_id="A1").last_seen_at == later
    assert IngestionRun.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("records,expected", [(True, 2), (False, 0)])
def test_price_change_appends_history_only_when_allowed(milk, records, expected):
    q = milk.search[0]
    ingest(FakeConnector({q: SearchResult([raw(price="6.58")], 0)}, records_price_history=records), milk, NOW, off_lookup=no_off)
    ingest(FakeConnector({q: SearchResult([raw(price="6.58")], 0)}, records_price_history=records), milk, NOW, off_lookup=no_off)
    ingest(FakeConnector({q: SearchResult([raw(price="5.99")], 0)}, records_price_history=records), milk, NOW + timedelta(hours=12), off_lookup=no_off)
    assert PriceHistory.objects.count() == expected
    assert Offer.objects.get().price == Decimal("5.99")


@pytest.mark.django_db
def test_duplicate_item_across_queries_processed_once(milk):
    assert len(milk.search) >= 2
    conn = FakeConnector(all_queries(milk, SearchResult([raw()], 1)))
    run = ingest(conn, milk, NOW, off_lookup=no_off)
    assert run.fetched == 1
    assert run.matched == 1
    assert run.skipped == len(milk.search)
    assert Offer.objects.count() == 1
    assert PriceHistory.objects.count() == 1


@pytest.mark.django_db
def test_unresolved_creates_review_items_and_resolution_closes_them(milk):
    q = milk.search[0]
    bad = raw(title="Organic Valley Reduced Fat Organic 2% Milk", item="U1", brand=None)
    ingest(FakeConnector({q: SearchResult([bad], 0)}), milk, NOW, off_lookup=no_off)
    open_details = set(ReviewItem.objects.filter(status="open").values_list("detail", flat=True))
    assert {"quantity", "attribute:brand_tier"} <= open_details

    ingest(FakeConnector({q: SearchResult([bad], 0)}), milk, NOW, off_lookup=no_off)
    assert ReviewItem.objects.count() == len(open_details)

    fixed = raw(title="Organic Valley Reduced Fat Organic 2% Milk, 2.8 L", item="U1", brand="Organic Valley")
    ingest(FakeConnector({q: SearchResult([fixed], 0)}), milk, NOW, off_lookup=no_off)
    assert not ReviewItem.objects.filter(status="open").exists()
    items = ReviewItem.objects.all()
    assert items.count() == len(open_details)
    assert all(i.status == "resolved" and i.resolved_at == NOW for i in items)


@pytest.mark.django_db
def test_upc_identity_merges_across_retailers(milk):
    q = milk.search[0]
    a = raw(retailer="r1", item="X", upc="0068826718115")
    b = raw(retailer="r2", item="Y", upc="068826718115")
    assert product_identity(a) == product_identity(b) == "upc:0068826718115"
    assert product_identity(raw()) == "fake:A1"
    ingest(FakeConnector({q: SearchResult([a], 0)}, name="r1"), milk, NOW, off_lookup=no_off)
    ingest(FakeConnector({q: SearchResult([b], 0)}, name="r2"), milk, NOW, off_lookup=no_off)
    assert Product.objects.count() == 1
    assert Product.objects.get().upc == "0068826718115"
    assert Offer.objects.count() == 2


@pytest.mark.django_db
def test_connector_error_marks_run_failed(milk):
    conn = FakeConnector({}, error=ConnectorError("HTTP 503"))
    run = ingest(conn, milk, NOW, off_lookup=no_off)
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.error == "HTTP 503"
    assert run.finished_at is not None
    assert Product.objects.count() == 0


@pytest.mark.django_db
def test_off_lookup_feeds_normalizer(milk):
    q = milk.search[0]
    r = raw(upc="0068826718115")
    seen = []

    def off(upc):
        seen.append(upc)
        return OffProduct(labels=frozenset({"en:organic"}))

    ingest(FakeConnector({q: SearchResult([r], 0)}), milk, NOW, off_lookup=off)
    assert seen == ["0068826718115"]
    assert Product.objects.get().attributes["organic"]["value"] is True


@pytest.mark.django_db
def test_bad_offer_is_skipped_and_run_continues(milk):
    q = milk.search[0]
    good = raw(item="G1")
    boom = raw(item="B1")
    boom = RawOffer(**{**boom.__dict__, "price": Decimal("12345678901.00")})
    conn = FakeConnector({q: SearchResult([boom, good], 0)})
    run = ingest(conn, milk, NOW, off_lookup=no_off)
    assert run.status == "succeeded"
    assert run.skipped == 1
    assert run.error
    assert Offer.objects.filter(retailer_item_id="G1").exists()
    assert not Offer.objects.filter(retailer_item_id="B1").exists()
