from decimal import Decimal
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.conf import settings
from django.core.management import call_command

from catalog.categories_sync import sync_categories
from catalog.ingest import ingest
from catalog.merge import merge_product
from catalog.models import Category, OffEntry, Offer, PriceHistory, Product, ReviewItem
from connectors.base import SearchResult
from connectors.recording import recorded_transport
from connectors.walmart import WalmartConnector
from normalize.types import OffProduct
from tests.catalog.helpers import LACTAID, NOW, FakeConnector, no_off, raw

GENERIC = "Simply Lemonade, 64 fl oz"  # matches no category


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


def run_ingest(milk, offers, off_lookup=no_off, **kw):
    conn = FakeConnector({milk.search[0]: SearchResult(offers=offers, skipped=0)}, **kw)
    return ingest(conn, milk, NOW, off_lookup=off_lookup)


def test_scheduled_ingest_keeps_unmatched(milk):
    run = run_ingest(milk, [raw(title=GENERIC, item="O1", brand="Simply")])
    offer = Offer.objects.get(retailer_item_id="O1")
    assert offer.category is None and offer.product.category is None
    assert offer.measure_dim == "volume"
    assert offer.generic_unit_price is not None
    assert offer.attributes == {"brand_tier": {"value": "name_brand", "source": "rule"}}
    assert offer.unit_prices == {} and offer.unresolved == ["category"]
    assert (run.matched, run.skipped) == (0, 0)
    assert run.status == "succeeded"


def test_curated_offer_gets_generic_fields(milk):
    run_ingest(milk, [raw()])
    offer = Offer.objects.get(retailer_item_id="A1")
    assert offer.unit_prices == {"per_volume": "0.002350"}
    assert offer.measure_dim == "volume"
    assert offer.generic_unit_price == (Decimal("6.58") / Decimal(str(offer.measure_amount))).quantize(
        Decimal("0.000001")
    )
    assert offer.measure_amount == pytest.approx(2800)


def test_generic_product_opens_no_review_item(milk):
    run_ingest(milk, [raw(title=GENERIC, item="O1", brand="Simply")])
    merge_product(Product.objects.get(identity="fake:O1"), NOW)
    assert ReviewItem.objects.count() == 0


def test_walmart_category_path(milk):
    transport = recorded_transport(Path(__file__).parents[1] / "connectors" / "recordings" / "walmart")
    from tests.connectors.test_walmart import CONSUMER, _der_b64  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    conn = WalmartConnector(
        CONSUMER, _der_b64(key), "7", client=httpx.Client(transport=httpx.MockTransport(transport.handler))
    )

    class Recorded:  # the recording only holds the "milk" query
        name = conn.name
        records_price_history = conn.records_price_history

        def search(self, query, limit=25):
            return conn.search("milk")

    ingest(Recorded(), milk, NOW, off_lookup=no_off)
    assert Offer.objects.filter(retailer="walmart").exists()
    assert set(Offer.objects.filter(retailer="walmart").values_list("category_path", flat=True)) == {
        "Food/Dairy, Eggs & Cheese/Milk"
    }


def test_labels_and_off_path_from_off_entry(milk):
    OffEntry.objects.create(
        upc="0093966010015", source="off", labels=["en:organic"], categories=["en:dairies", "en:milks"]
    )
    conn = FakeConnector({milk.search[0]: SearchResult([raw(upc="0093966010015")], 0)})
    from enrichment.off import lookup

    ingest(conn, milk, NOW, off_lookup=lookup)
    offer = Offer.objects.get()
    assert offer.labels == ["en:organic"]
    assert offer.category_path == "Open Food Facts/Dairies/Milks"


def test_walmart_never_in_price_history_generic(milk):
    conn = FakeConnector(
        {milk.search[0]: SearchResult([raw(title=GENERIC, item="O1", retailer="walmart")], 0)},
        records_price_history=False,
    )
    ingest(conn, milk, NOW, off_lookup=no_off)
    assert Offer.objects.count() == 1 and PriceHistory.objects.count() == 0


def test_backfill_generic_idempotent(milk):
    OffEntry.objects.create(upc="0093966010015", source="off", labels=["en:organic"], quantity="2.8 L")
    from enrichment.off import lookup

    run_ingest(
        milk,
        [raw(upc="0093966010015"), raw(title=GENERIC, item="O1", brand="Simply")],
        off_lookup=lookup,
    )
    want = {
        o.pk: (o.measure_dim, o.measure_amount, o.generic_unit_price, o.labels)
        for o in Offer.objects.all()
    }
    Offer.objects.update(measure_dim=None, measure_amount=None, generic_unit_price=None, labels=[])
    for args, count in ((("--batch", "1"), 2), (("--batch", "1"), 0), (("--all",), 2), (("--all",), 2)):
        out = StringIO()
        call_command("backfill_generic", *args, stdout=out)
        assert out.getvalue().strip() == f"updated={count}"
        got = {
            o.pk: (o.measure_dim, o.measure_amount, o.generic_unit_price, o.labels)
            for o in Offer.objects.all()
        }
        assert got == want


def test_backfill_skips_computed_unknown_measures(milk):
    run_ingest(milk, [raw(title="Mystery Milk", item="M1", brand=None)])
    offer = Offer.objects.get()
    assert offer.measure_dim is None and offer.measure_reason
    out = StringIO()
    call_command("backfill_generic", stdout=out)
    assert out.getvalue().strip() == "updated=0"


def test_backfill_uses_fresh_price(milk):
    from catalog.management.commands.backfill_generic import recompute

    run_ingest(milk, [raw(item="A1")])
    stale = Offer.objects.get()
    Offer.objects.update(price=Decimal("10.00"))  # concurrent ingest after the stale read
    recompute(stale)
    fresh = Offer.objects.get()
    assert fresh.price == Decimal("10.00")
    assert fresh.generic_unit_price == (Decimal("10.00") / Decimal("2800")).quantize(Decimal("0.000001"))


def _cat(offer):
    return Offer.objects.get(retailer_item_id=offer).category


def test_generic_then_curated_gains_category(milk):
    run_ingest(milk, [raw(title=GENERIC, item="G1", upc="0093966010015", brand=None)])
    product = Product.objects.get()
    assert product.category is None
    run_ingest(milk, [raw(item="C1", upc="0093966010015", retailer="other")])
    product.refresh_from_db()
    assert product.category == milk
    assert ReviewItem.objects.filter(detail="category").count() == 0


def test_curated_then_generic_keeps_category(milk):
    run_ingest(milk, [raw(item="C1", upc="0093966010015", retailer="zeta")])
    # a generic offer from a retailer that sorts first
    run_ingest(milk, [raw(title=GENERIC, item="G1", upc="0093966010015", retailer="alpha", brand=None)])
    product = Product.objects.get()
    assert product.category == milk
    assert not ReviewItem.objects.filter(detail="category").exists()


def test_curated_offer_reingested_as_unmatched_keeps_product_category(milk):
    run_ingest(milk, [raw(item="C1", upc="0093966010015", retailer="a"), raw(item="C2", upc="0093966010015", retailer="b")])
    run_ingest(milk, [raw(title=GENERIC, item="C1", upc="0093966010015", retailer="a", brand=None)])
    assert Offer.objects.get(retailer="a").category is None
    assert Product.objects.get().category == milk
    assert not ReviewItem.objects.filter(detail="category").exists()


def _with_shipping(value):
    import dataclasses

    base = raw()
    return dataclasses.replace(base, fields={"shipping": value} if value is not None else {})


def test_ingest_sets_and_clears_shipping(milk):
    run_ingest(milk, [_with_shipping("5.99")])
    assert Offer.objects.get(retailer_item_id="A1").shipping == Decimal("5.99")
    run_ingest(milk, [_with_shipping("0")])
    assert Offer.objects.get(retailer_item_id="A1").shipping == Decimal("0")
    run_ingest(milk, [_with_shipping(None)])
    assert Offer.objects.get(retailer_item_id="A1").shipping is None
