"""Held unit-price outliers (#127) in browse and on the category page."""

import re
from datetime import date
from decimal import Decimal

import pytest

from catalog.browse import FilterSpec, browse
from catalog.models import Category, Offer, Product, Store
from catalog.outliers import approve, flag_unit_price_outliers
from tests.catalog.helpers import NOW

NONE = FilterSpec(attributes={})
HELD = "size looks wrong (held for review)"


@pytest.fixture
def rice(milk):
    return Category.objects.get(slug="pantry/rice")


@pytest.fixture
def tp(milk):
    return Category.objects.get(slug="household/toilet-paper")


def live(
    category, n, unit_prices, retailer="walmart", product=None, price="3.00", title=None
):
    title = title or f"Item {n}"
    product = product or Product.objects.create(
        identity=f"{category.slug}-{n}", title=title, category=category
    )
    return Offer.objects.create(
        product=product,
        retailer=retailer,
        retailer_item_id=f"i{n}",
        price=Decimal(price),
        currency="USD",
        url=f"https://example.com/{n}",
        category=category,
        title=title,
        measures={"mass_g": 1000.0},
        unit_prices=unit_prices,
        last_seen_at=NOW,
        kind="live",
    )


def chips(category, n=9):
    store = Store.objects.create(
        code=f"osm-{n}",
        name="Safeway",
        city="Mountain View",
        chain_code="op-safeway",
        chain_name="Safeway",
        country_code="US",
        osm_type="node",
        osm_id=n,
    )
    product = Product.objects.create(
        identity=f"chips-{n}",
        title="Organic Fairtrade 45% Cacao Baking Chips",
        category=category,
    )
    return Offer.objects.create(
        product=product,
        retailer="op-safeway",
        retailer_item_id=f"o{n}@{store.code}",
        price=Decimal("8.99"),
        currency="USD",
        url="https://prices.openfoodfacts.org/prices/1",
        category=category,
        title=product.title,
        measures={"mass_g": 3260.0},
        unit_prices={"per_mass": "0.0001"},
        last_seen_at=NOW,
        kind="observed",
        observed_on=date(2026, 11, 6),
        store=store,
    )


def rice_rows(rice):
    for n, per_gram in enumerate(["0.002", "0.003", "0.004", "0.005"], start=1):
        live(rice, n, {"per_mass": per_gram})


def test_outlier_is_unranked_with_reason(rice):
    rice_rows(rice)
    held = chips(rice)
    flag_unit_price_outliers(NOW)
    res = browse(rice, NONE, None, None, NOW, observed=True)
    assert res.ranked == []
    [row] = res.unranked
    assert row.offer == held and row.reason == HELD and row.unit_price is None
    # unflagged, it would rank
    Offer.objects.filter(pk=held.pk).update(held_comparisons={})
    assert browse(rice, NONE, None, None, NOW, observed=True).ranked[0].offer == held


def test_product_with_another_normal_offer_ranks_by_it(rice):
    rice_rows(rice)
    bad = live(rice, 7, {"per_mass": "0.0001"}, retailer="target", price="1.00")
    good = live(rice, 8, {"per_mass": "0.006"}, retailer="kroger", product=bad.product)
    flag_unit_price_outliers(NOW)
    res = browse(rice, NONE, None, None, NOW)
    [row] = [r for r in res.ranked if r.product == bad.product]
    assert row.offer == good and row.unit_price == Decimal("0.006")
    assert res.ranked[0].unit_price == Decimal("0.002")
    assert all(r.product != bad.product for r in res.unranked)


def test_alternate_comparison_unaffected_unless_also_outlier(tp):
    for n in range(1, 5):
        live(tp, n, {"area_x_ply": "0.5", "per_roll": "1.0"})
    odd = live(tp, 5, {"area_x_ply": "0.01", "per_roll": "0.9"})
    flag_unit_price_outliers(NOW)
    default = browse(tp, NONE, None, None, NOW)
    assert [r.reason for r in default.unranked if r.offer == odd] == [HELD]
    per_roll = browse(tp, NONE, "per_roll", None, NOW)
    assert per_roll.ranked[0].offer == odd and per_roll.ranked[0].unit_price == Decimal(
        "0.9"
    )


def test_approved_offer_ranks_again(rice):
    rice_rows(rice)
    held = chips(rice)
    flag_unit_price_outliers(NOW)
    approve(held, "per_mass")
    flag_unit_price_outliers(NOW)
    assert browse(rice, NONE, None, None, NOW, observed=True).ranked[0].offer == held


def test_nutrition_rows_never_show_a_held_unit_price(milk):
    rice = Category.objects.get(slug="pantry/rice")
    rice_rows(rice)
    held = chips(rice)
    Offer.objects.filter(pk=held.pk).update(nutrition={"protein_cost": "0.01"})
    flag_unit_price_outliers(NOW)
    res = browse(rice, NONE, None, None, NOW, observed=True, rank="protein")
    [row] = res.ranked
    assert row.offer == held and row.unit_price is None


# --- category page ----------------------------------------------------------------


def summary_of(html):
    m = re.search(r'<p class="category-summary">(.*?)</p>', html, re.S)
    return " ".join(m.group(1).split()) if m else None


def test_category_page_uses_next_real_lowest_price(client, rice, frozen_now):
    rice_rows(rice)
    chips(rice)
    flag_unit_price_outliers(NOW)
    html = client.get("/c/pantry/rice/").content.decode()
    # $0.002/g = $0.0567/oz at Walmart, not the chips' $0.0001/g
    assert (
        '<meta name="description" content="5 prices from 2 stores. Lowest seen: $0.0567/oz at '
        'Walmart (Nov 7, 2026). Compared by price per ounce.">'
    ) in html
    assert summary_of(html).startswith(
        "5 prices from 2 stores. Lowest seen: $0.0567/oz at Walmart"
    )
    assert HELD in html
