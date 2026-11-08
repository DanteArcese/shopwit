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
from tests.catalog.helpers import LACTAID, NOW, FakeConnector, no_off, raw


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
    assert p.attributes["fat"]["value"] == "2"
    assert p.attributes["fat"]["source"] in {"api", "off", "rule", "manual"}
    assert p.measures == {"volume_ml": 2800}
    o = Offer.objects.get(retailer="fake", retailer_item_id="A1")
    assert o.product == p
    assert o.price == Decimal("6.58")
    assert o.currency == "USD"
    assert o.unit_prices == {"per_volume": "0.002350"}
    assert o.url == "https://example.com/fake/A1"
    assert o.image_url is None
    assert o.last_seen_at == NOW
    assert Product.objects.count() == 3  # the hose is kept as a generic product
    assert Product.objects.get(identity="fake:J1").category is None


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


def _fat_offer(retailer, item, fat, **kw):
    return raw(retailer=retailer, item=item, title=f"Lactaid Reduced Fat {fat}% Milk, 2.8 L", **kw)


def _ingest_two(milk, offers_a, offers_b, now=NOW):
    q = milk.search[0]
    ingest(FakeConnector({q: SearchResult(offers_a, 0)}, name="r1"), milk, now, off_lookup=no_off)
    ingest(FakeConnector({q: SearchResult(offers_b, 0)}, name="r2"), milk, now, off_lookup=no_off)


UPC = "0068826718115"


@pytest.mark.django_db
def test_merged_product_review_items_are_stable_across_runs(milk):
    q = milk.search[0]
    # r1 resolves everything; r2 lacks quantity and brand
    a = raw(retailer="r1", item="X", upc=UPC)
    b = raw(retailer="r2", item="Y", upc=UPC, title="Lactaid Reduced Fat 2% Milk", brand=None)
    snaps = []
    for i in range(3):
        now = NOW + timedelta(hours=12 * i)
        ingest(FakeConnector({q: SearchResult([a], 0)}, name="r1"), milk, now, off_lookup=no_off)
        ingest(FakeConnector({q: SearchResult([b], 0)}, name="r2"), milk, now, off_lookup=no_off)
        snaps.append(sorted(ReviewItem.objects.values_list("detail", "status")))
    assert snaps[1] == snaps[2]
    assert Product.objects.count() == 1
    # quantity and brand_tier resolved by r1 count as resolved
    assert not any(d in ("quantity", "attribute:brand_tier") for d, st in snaps[2] if st == "open")


@pytest.mark.django_db
def test_conflicting_attribute_omitted_and_flagged(milk):
    _ingest_two(
        milk,
        [_fat_offer("r1", "X", 2, upc=UPC)],
        [_fat_offer("r2", "Y", 1, upc=UPC)],
    )
    p = Product.objects.get()
    assert "fat" not in p.attributes
    assert ReviewItem.objects.filter(product=p, detail="attribute:fat:conflict", status="open").exists()


@pytest.mark.django_db
def test_same_attribute_value_from_both_is_kept(milk):
    _ingest_two(
        milk,
        [_fat_offer("r1", "X", 2, upc=UPC)],
        [_fat_offer("r2", "Y", 2, upc=UPC)],
    )
    p = Product.objects.get()
    assert p.attributes["fat"]["value"] == "2"
    assert not ReviewItem.objects.filter(detail="attribute:fat:conflict").exists()


@pytest.mark.django_db
def test_offer_order_does_not_change_product(milk):
    def run():
        Offer.objects.all().delete()
        Product.objects.all().delete()
        ReviewItem.objects.all().delete()

    a = _fat_offer("r1", "X", 2, upc=UPC)
    b = raw(retailer="r2", item="Y", upc=UPC, title="Lactaid Whole Milk 1 L", brand=None)

    def state():
        p = Product.objects.get()
        return (p.title, p.brand, p.attributes, p.measures, sorted(ReviewItem.objects.filter(status="open").values_list("detail", flat=True)))

    _ingest_two(milk, [a], [b])
    first = state()
    run()
    _ingest_two(milk, [b], [a])
    # r1/r2 connectors differ by call order only; the merge is retailer ordered
    assert state() == first


@pytest.mark.django_db
def test_connector_error_on_later_query_reports_partial_fetch(milk):
    assert len(milk.search) >= 2

    class Flaky(FakeConnector):
        def search(self, query, limit=25):
            if query == milk.search[1]:
                raise ConnectorError("HTTP 500")
            return SearchResult([raw()], 3)

    run = ingest(Flaky({}), milk, NOW, off_lookup=no_off)
    assert run.status == "failed"
    assert run.fetched == 1
    assert run.skipped == 3
    assert (run.matched, run.fully_parsed, run.unresolved) == (0, 0, 0)
    assert Product.objects.count() == 0


@pytest.mark.django_db
def test_reingest_updates_title_brand_category(milk):
    q = milk.search[0]
    ingest(FakeConnector({q: SearchResult([raw()], 0)}), milk, NOW, off_lookup=no_off)
    changed = raw(title="Lactaid Reduced Fat 2% Milk, 1 L", brand="Lactaid Co")
    ingest(FakeConnector({q: SearchResult([changed], 0)}), milk, NOW, off_lookup=no_off)
    p = Product.objects.get()
    assert p.title == "Lactaid Reduced Fat 2% Milk, 1 L"
    assert p.brand == "Lactaid Co"
    assert p.category == milk


@pytest.mark.django_db
def test_product_matched_to_other_category_uses_its_own(milk):
    eggs = Category.objects.get(slug="dairy/eggs")
    q = milk.search[0]
    egg = raw(title="Large Grade A Eggs, 12 ct", item="E1", brand="Eggland's Best")
    run = ingest(FakeConnector({q: SearchResult([egg], 0)}), milk, NOW, off_lookup=no_off)
    assert run.matched == 1
    assert Product.objects.get().category == eggs
