from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError

from catalog.categories_sync import sync_categories
from catalog.forms import validate_overrides
from catalog.ingest import ingest
from catalog.merge import merge_product
from catalog.models import Category, Offer, Product, ReviewItem
from connectors.base import SearchResult
from normalize.types import RawOffer

from test_ingest import FakeConnector, no_off, raw

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)


def make_offer(product, category, item, retailer="a", attrs=None, measures=None, unresolved=(), price="10.00"):
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal(price), currency="USD",
        url="https://example.com/x", category=category, title="t", attributes=attrs or {},
        measures=measures or {}, unresolved=list(unresolved), last_seen_at=NOW,
    )


def attr(v):
    return {"value": v, "source": "rule"}


def open_details(product):
    return set(product.review_items.filter(status="open").values_list("detail", flat=True))


def test_override_wins_and_resolves_review_item(cats):
    milk = Category.objects.get(slug="dairy/milk")
    p = Product.objects.create(identity="x:1", title="Milk", category=milk)
    make_offer(p, milk, "1", measures={"volume_ml": 2800}, unresolved=["attribute:fat:ambiguous"])
    merge_product(p, NOW)
    assert "attribute:fat:ambiguous" in open_details(p)

    p.manual_attributes = {"fat": "2"}
    p.save()
    merge_product(p, NOW)
    p.refresh_from_db()
    assert p.attributes["fat"] == {"value": "2", "source": "manual"}
    assert "attribute:fat:ambiguous" not in open_details(p)
    assert p.review_items.get(detail="attribute:fat:ambiguous").status == "resolved"


def test_override_resolves_conflict_between_retailers(cats):
    milk = Category.objects.get(slug="dairy/milk")
    p = Product.objects.create(identity="x:2", title="Milk", category=milk)
    make_offer(p, milk, "1", retailer="a", attrs={"fat": attr("2")})
    make_offer(p, milk, "2", retailer="b", attrs={"fat": attr("whole")})
    merge_product(p, NOW)
    assert "attribute:fat:conflict" in open_details(p)

    p.manual_attributes = {"fat": "whole"}
    p.save()
    merge_product(p, NOW)
    p.refresh_from_db()
    assert p.attributes["fat"]["source"] == "manual"
    assert "attribute:fat:conflict" not in open_details(p)


def test_reingest_does_not_reopen_overridden_item(cats):
    milk = Category.objects.get(slug="dairy/milk")
    q = milk.search[0]
    bad = raw(title="Organic Valley Reduced Fat Organic 2% Milk, 2.8 L", item="U1", brand=None)
    conn = FakeConnector({q: SearchResult([bad], 0)})
    ingest(conn, milk, NOW, off_lookup=no_off)
    p = Product.objects.get(identity="fake:U1")
    assert "attribute:brand_tier" in open_details(p)

    p.manual_attributes = {"brand_tier": "name_brand"}
    p.save()
    merge_product(p, NOW)
    assert "attribute:brand_tier" not in open_details(p)

    ingest(conn, milk, NOW, off_lookup=no_off)
    p.refresh_from_db()
    assert p.attributes["brand_tier"] == {"value": "name_brand", "source": "manual"}
    assert "attribute:brand_tier" not in open_details(p)
    assert not ReviewItem.objects.filter(product=p, detail="attribute:brand_tier", status="open").exists()


def test_override_recomputes_unit_prices(cats):
    tp = Category.objects.get(slug="household/toilet-paper")
    p = Product.objects.create(identity="x:3", title="TP", category=tp)
    o = make_offer(
        p, tp, "1", measures={"area_m2": 10.0, "sheets": 200, "rolls": 4},
        unresolved=["attribute:ply", "unit_price:area_x_ply"], price="20.00",
        attrs={"brand_tier": attr("name_brand")},
    )
    merge_product(p, NOW)
    assert "unit_price:area_x_ply" in open_details(p)

    p.manual_attributes = {"ply": 2}
    p.save()
    merge_product(p, NOW)
    o.refresh_from_db()
    assert Decimal(o.unit_prices["area_x_ply"]) == Decimal("1")
    assert "unit_price:area_x_ply" not in o.unresolved
    assert "unit_price:area_x_ply" not in open_details(p)


def test_invalid_override_rejected(cats):
    milk = Category.objects.get(slug="dairy/milk")
    tp = Category.objects.get(slug="household/toilet-paper")
    assert validate_overrides(milk, {"fat": "2", "organic": True, "brand_tier": "store_brand"}) == {
        "fat": "2", "organic": True, "brand_tier": "store_brand",
    }
    assert validate_overrides(tp, {"ply": 3}) == {"ply": 3}
    for cat, bad in [
        (milk, {"fat": "5"}), (milk, {"nope": 1}), (milk, {"organic": "yes"}),
        (milk, {"brand_tier": "premium"}), (tp, {"ply": "2"}), (tp, {"ply": True}), (tp, {"ply": 4}),
    ]:
        with pytest.raises(ValidationError):
            validate_overrides(cat, bad)
    with pytest.raises(ValidationError, match="nope"):
        validate_overrides(milk, {"nope": 1})
