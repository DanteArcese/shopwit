from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings

from catalog.browse import FilterSpec, browse, parse_filters
from catalog.categories_sync import sync_categories
from catalog.ingest import ingest
from catalog.models import Category
from catalog.retailers import available_retailers, display_name
from connectors.base import SearchResult
from tests.catalog.helpers import NOW, FakeConnector, no_off, raw

NONE = FilterSpec(attributes={})


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


@pytest.fixture
def tp(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="household/toilet-paper")


def load(category, offers, now=NOW):
    conn = FakeConnector({q: SearchResult(offers=offers, skipped=0) for q in category.search})
    ingest(conn, category, now, off_lookup=no_off)


def milk_offer(title, price, item, **kw):
    return raw(title=title, price=price, item=item, **kw)


def titles(rows):
    return [r.product.title for r in rows]


def test_rank_by_unit_price(milk):
    load(milk, [
        milk_offer("Acme Whole Milk, 1 L", "3.00", "a"),
        milk_offer("Bravo Whole Milk, 1 L", "1.00", "b"),
        milk_offer("Cargo Whole Milk, 1 L", "2.00", "c"),
    ])
    res = browse(milk, NONE, None, None, NOW)
    assert titles(res.ranked) == [
        "Bravo Whole Milk, 1 L", "Cargo Whole Milk, 1 L", "Acme Whole Milk, 1 L"
    ]
    assert res.comparison == "per_volume"
    assert res.ranked[0].unit_price == Decimal("0.001000")
    assert res.total_products == 3


def test_unknown_excluded_when_filtered_and_labelled_otherwise(milk):
    load(milk, [
        milk_offer("Acme Whole Milk, 1 L", "1.00", "a"),
        milk_offer("Bravo Milk, 1 L", "0.50", "b"),  # fat unknown
    ])
    all_rows = browse(milk, NONE, None, None, NOW).ranked
    assert titles(all_rows) == ["Bravo Milk, 1 L", "Acme Whole Milk, 1 L"]
    assert "fat" in all_rows[0].unknown
    assert "fat" not in all_rows[1].unknown

    filtered = browse(milk, FilterSpec(attributes={"fat": "whole"}), None, None, NOW)
    assert titles(filtered.ranked) == ["Acme Whole Milk, 1 L"]
    assert filtered.total_products == 1
    assert "fat" not in filtered.ranked[0].unknown


def test_stale_offers_never_shown(milk):
    old = NOW - timedelta(hours=48)
    # same product (same UPC) at two retailers; the cheap one is stale
    load(milk, [milk_offer("Acme Whole Milk, 1 L", "0.50", "s", retailer="target", upc="036000291452")], old)
    load(milk, [milk_offer("Acme Whole Milk, 1 L", "2.00", "f", retailer="walmart", upc="036000291452")])
    load(milk, [milk_offer("Gone Whole Milk, 1 L", "0.10", "g", retailer="target")], old)
    res = browse(milk, NONE, None, None, NOW)
    assert titles(res.ranked + res.unranked) == ["Acme Whole Milk, 1 L"]
    assert res.ranked[0].offer.retailer == "walmart"
    assert res.total_products == 1
    assert available_retailers(NOW) == ["walmart"]


def test_unranked_lists_reason(milk):
    load(milk, [
        milk_offer("Acme Whole Milk, 1 L", "1.00", "a"),
        milk_offer("Mystery Whole Milk", "0.50", "b"),
    ])
    res = browse(milk, NONE, None, None, NOW)
    assert titles(res.ranked) == ["Acme Whole Milk, 1 L"]
    assert titles(res.unranked) == ["Mystery Whole Milk"]
    assert res.unranked[0].reason == "size unknown"
    assert res.unranked[0].unit_price is None
    assert res.total_products == 2


def test_store_filter(milk):
    load(milk, [
        milk_offer("Acme Whole Milk, 1 L", "1.00", "a", retailer="walmart"),
        milk_offer("Bravo Whole Milk, 1 L", "2.00", "b", retailer="target"),
    ])
    res = browse(milk, NONE, None, frozenset({"target"}), NOW)
    assert titles(res.ranked) == ["Bravo Whole Milk, 1 L"]
    assert len(browse(milk, NONE, None, frozenset(), NOW).ranked) == 0
    assert len(browse(milk, NONE, None, None, NOW).ranked) == 2


def test_cheapest_fresh_offer_per_product(milk):
    upc = "036000291452"
    load(milk, [
        milk_offer("Acme Whole Milk, 1 L", "3.00", "a", retailer="walmart", upc=upc),
        milk_offer("Acme Whole Milk, 1 L", "2.00", "b", retailer="target", upc=upc),
    ])
    res = browse(milk, NONE, None, None, NOW)
    assert len(res.ranked) == 1
    assert res.ranked[0].offer.retailer == "target"


def test_alternate_comparison_and_invalid_falls_back(tp):
    load(tp, [
        raw(title="Acme Toilet Paper 2 ply, 6 rolls", price="6.00", item="a", brand="Acme"),
        raw(title="Bravo Toilet Paper 2 ply, 12 rolls", price="9.00", item="b", brand="Bravo"),
    ])
    default = browse(tp, NONE, None, None, NOW)
    assert default.comparison == "area_x_ply"
    per_roll = browse(tp, NONE, "per_roll", None, NOW)
    assert per_roll.comparison == "per_roll"
    assert titles(per_roll.ranked)[0] == "Bravo Toilet Paper 2 ply, 12 rolls"
    assert per_roll.ranked[0].unit_price == Decimal("0.750000")
    bad = browse(tp, NONE, "per_volume", None, NOW)
    assert bad.comparison == "area_x_ply"


def test_unranked_ply_reason(tp):
    load(tp, [raw(title="Acme Toilet Paper, 6 rolls, 100 sq ft", price="6.00", item="a", brand="Acme")])
    res = browse(tp, NONE, None, None, NOW)
    assert res.ranked == []
    assert res.unranked[0].reason == "ply unknown"


def test_invalid_params_ignored(milk, tp):
    assert parse_filters(milk, {"fat": "banana"}).attributes == {}
    assert parse_filters(milk, {"fat": "any", "organic": "maybe"}).attributes == {}
    assert parse_filters(milk, {"fat": "whole", "organic": "true", "lactose_free": "false"}).attributes == {
        "fat": "whole", "organic": True, "lactose_free": False,
    }
    assert parse_filters(tp, {"ply": "2"}).attributes == {"ply": 2}
    assert parse_filters(tp, {"ply": "9"}).attributes == {}
    assert parse_filters(tp, {"ply": "x"}).attributes == {}
    spec = parse_filters(milk, {"size_min": "abc", "size_max": "500", "brand_tier": "nope"})
    assert (spec.size_min, spec.size_max, spec.brand_tier) == (None, 500.0, None)
    assert parse_filters(milk, {"brand_tier": "store_brand"}).brand_tier == "store_brand"


def test_ties_break_deterministically(milk):
    load(milk, [
        milk_offer("Zed Whole Milk, 1 L", "1.00", "z", retailer="walmart"),
        milk_offer("Abe Whole Milk, 1 L", "1.00", "a", retailer="target"),
        milk_offer("Mid Whole Milk, 2 L", "1.90", "m"),  # same unit price as nothing; just cheaper per mL
    ])
    res = browse(milk, NONE, None, None, NOW)
    assert titles(res.ranked) == [
        "Mid Whole Milk, 2 L", "Abe Whole Milk, 1 L", "Zed Whole Milk, 1 L"
    ]
    # equal unit price and title: price, then retailer
    load(milk, [
        milk_offer("Twin Whole Milk, 1 L", "1.50", "t1", retailer="walmart"),
        milk_offer("Twin Whole Milk, 1 L", "1.50", "t2", retailer="target"),
    ])
    twins = [r for r in browse(milk, NONE, None, None, NOW).ranked if r.product.title.startswith("Twin")]
    assert [r.offer.retailer for r in twins] == ["target", "walmart"]


def test_brand_tier_filter(milk):
    load(milk, [
        milk_offer("Great Value Whole Milk, 1 L", "1.00", "g", brand="Great Value"),
        milk_offer("Acme Whole Milk, 1 L", "2.00", "a", brand="Acme"),
    ])
    store = browse(milk, FilterSpec(attributes={}, brand_tier="store_brand"), None, None, NOW)
    assert titles(store.ranked) == ["Great Value Whole Milk, 1 L"]
    name = browse(milk, FilterSpec(attributes={}, brand_tier="name_brand"), None, None, NOW)
    assert titles(name.ranked) == ["Acme Whole Milk, 1 L"]


def test_size_range_and_limit(milk):
    load(milk, [
        milk_offer("Small Whole Milk, 500 mL", "1.00", "s"),
        milk_offer("Big Whole Milk, 2 L", "3.00", "b"),
        milk_offer("Mystery Whole Milk", "1.00", "m"),
    ])
    spec = FilterSpec(attributes={}, size_min=1000)
    res = browse(milk, spec, None, None, NOW)
    assert titles(res.ranked) == ["Big Whole Milk, 2 L"]
    assert res.unranked == []  # no size: excluded when a bound is set
    assert titles(browse(milk, FilterSpec(attributes={}, size_max=1000), None, None, NOW).ranked) == [
        "Small Whole Milk, 500 mL"
    ]
    capped = browse(milk, NONE, None, None, NOW, limit=1)
    assert len(capped.ranked) == 1 and capped.total_products == 3


def test_retailer_names():
    assert display_name("shaws") == "Shaw's"
    assert display_name("newmart") == "Newmart"


def test_query_count_is_flat(milk, django_assert_max_num_queries):
    load(milk, [milk_offer(f"B{i} Whole Milk, 1 L", "1.00", f"i{i}") for i in range(10)])
    with django_assert_max_num_queries(4):
        browse(milk, NONE, None, None, NOW)
