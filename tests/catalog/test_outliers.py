"""Unit-price outlier guard (#127): rule, stored pass, review items, approval."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.management import call_command

from catalog.categories_sync import sync_categories
from catalog.models import Category, Offer, Product, ReviewItem, Store
from catalog.outliers import find_outliers, flag_unit_price_outliers, median
from tests.catalog.helpers import NOW

D = Decimal


# --- the pure rule ---------------------------------------------------------------


def test_median_odd_and_even():
    assert median([D("3"), D("1"), D("2")]) == D("2")
    assert median([D("4"), D("1"), D("3"), D("2")]) == D("2.5")


def test_fewer_than_five_offers_never_flag():
    prices = {1: D("0.0001"), 2: D("1"), 3: D("1"), 4: D("1")}
    assert find_outliers(prices) == (None, set())


def test_exactly_a_tenth_of_the_median_is_not_an_outlier():
    prices = {1: D("0.1"), 2: D("1"), 3: D("1"), 4: D("1"), 5: D("1")}
    assert find_outliers(prices) == (D("1"), set())


def test_just_below_a_tenth_is_an_outlier():
    prices = {1: D("0.0999999"), 2: D("1"), 3: D("1"), 4: D("1"), 5: D("1")}
    assert find_outliers(prices) == (D("1"), {1})


def test_even_count_uses_mean_of_middle_values():
    # sorted: 0.01, 1, 2, 4, 5, 6 -> median (2 + 4) / 2 = 3, cutoff 0.3
    prices = {1: D("0.01"), 2: D("1"), 3: D("2"), 4: D("4"), 5: D("5"), 6: D("6")}
    assert find_outliers(prices) == (D("3"), {1})
    prices[1] = D("0.3")
    assert find_outliers(prices) == (D("3"), set())


def test_high_side_is_never_flagged():
    prices = {1: D("1000"), 2: D("1"), 3: D("1"), 4: D("1"), 5: D("1")}
    assert find_outliers(prices)[1] == set()


# --- the stored pass ---------------------------------------------------------------


@pytest.fixture
def rice(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="pantry/rice")


@pytest.fixture
def tp(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="household/toilet-paper")


def live(category, n, unit_prices, retailer="walmart", last_seen=NOW, product=None):
    product = product or Product.objects.create(
        identity=f"{category.slug}-{n}", title=f"Item {n}", category=category
    )
    return Offer.objects.create(
        product=product,
        retailer=retailer,
        retailer_item_id=f"i{n}",
        price=D("3.00"),
        currency="USD",
        url=f"https://example.com/{n}",
        category=category,
        title=f"Item {n}",
        unit_prices=unit_prices,
        last_seen_at=last_seen,
        kind="live",
    )


def observed(category, n, unit_prices, on=date(2026, 11, 3)):
    store = Store.objects.create(
        code=f"osm-{n}",
        name="Safeway",
        chain_code="op-safeway",
        chain_name="Safeway",
        country_code="US",
        osm_type="node",
        osm_id=n,
    )
    product = Product.objects.create(
        identity=f"obs-{n}", title=f"Seen {n}", category=category
    )
    return Offer.objects.create(
        product=product,
        retailer="op-safeway",
        retailer_item_id=f"o{n}@{store.code}",
        price=D("8.99"),
        currency="USD",
        url="https://prices.openfoodfacts.org/prices/1",
        category=category,
        title=f"Seen {n}",
        unit_prices=unit_prices,
        last_seen_at=NOW,
        kind="observed",
        observed_on=on,
        store=store,
    )


def four_normal(rice):
    return [live(rice, n, {"per_mass": "0.01"}) for n in range(1, 5)]


def held(offer):
    offer.refresh_from_db()
    return offer.held_comparisons


def test_pass_flags_live_and_observed_together(rice):
    four_normal(rice)
    chips = observed(rice, 9, {"per_mass": "0.0009"})
    stats = flag_unit_price_outliers(NOW)
    assert held(chips) == {"per_mass": "0.01"}
    assert stats["pantry/rice"] == {"per_mass": 1}
    assert Offer.objects.exclude(pk=chips.pk).filter(held_comparisons={}).count() == 4
    # nothing about the offer itself is modified
    chips.refresh_from_db()
    assert chips.unit_prices == {"per_mass": "0.0009"} and chips.price == D("8.99")


def test_pass_ignores_stale_offers_and_needs_five(rice):
    four_normal(rice)
    live(rice, 5, {"per_mass": "0.01"}, last_seen=NOW - timedelta(days=30))  # not fresh
    low = live(rice, 6, {"per_mass": "0.0001"}, retailer="target")
    # 5 fresh offers including the low one: median 0.01, so it is flagged
    flag_unit_price_outliers(NOW)
    assert held(low) == {"per_mass": "0.01"}
    Offer.objects.filter(retailer_item_id="i1").update(last_seen_at=NOW - timedelta(days=30))
    flag_unit_price_outliers(
        NOW
    )  # only 4 fresh offers now: no rule, so the flag clears
    assert held(low) == {}


def test_pass_clears_when_data_changes(rice):
    four_normal(rice)
    low = live(rice, 6, {"per_mass": "0.0001"})
    flag_unit_price_outliers(NOW)
    assert held(low) == {"per_mass": "0.01"}
    Offer.objects.filter(pk=low.pk).update(
        unit_prices={"per_mass": "0.009"}
    )  # size fixed upstream
    flag_unit_price_outliers(NOW)
    assert held(low) == {}


def test_review_items_open_and_resolve_idempotently(rice):
    four_normal(rice)
    low = live(rice, 6, {"per_mass": "0.0001"})
    flag_unit_price_outliers(NOW)
    flag_unit_price_outliers(NOW)
    items = list(ReviewItem.objects.filter(reason=ReviewItem.Reason.OUTLIER))
    assert [(i.product_id, i.detail, i.status) for i in items] == [
        (low.product_id, f"outlier:per_mass:offer={low.pk}", "open")
    ]
    Offer.objects.filter(pk=low.pk).update(unit_prices={"per_mass": "0.009"})
    flag_unit_price_outliers(NOW)
    flag_unit_price_outliers(NOW)
    item = ReviewItem.objects.get(reason=ReviewItem.Reason.OUTLIER)
    assert item.status == ReviewItem.Status.RESOLVED and item.resolved_at is not None


def test_review_item_resolves_when_offer_is_deleted(rice):
    four_normal(rice)
    low = live(rice, 6, {"per_mass": "0.0001"})
    flag_unit_price_outliers(NOW)
    low.delete()
    flag_unit_price_outliers(NOW)
    assert ReviewItem.objects.get(reason=ReviewItem.Reason.OUTLIER).status == "resolved"


def test_approval_survives_reruns(rice):
    four_normal(rice)
    low = live(rice, 6, {"per_mass": "0.0001"})
    flag_unit_price_outliers(NOW)
    Offer.objects.filter(pk=low.pk).update(approved_comparisons=["per_mass"])
    flag_unit_price_outliers(NOW)
    flag_unit_price_outliers(NOW)
    assert held(low) == {}
    assert ReviewItem.objects.get(reason=ReviewItem.Reason.OUTLIER).status == "resolved"


def test_each_comparison_is_judged_on_its_own(tp):
    for n in range(1, 5):
        live(tp, n, {"area_x_ply": "0.5", "per_roll": "1.0"})
    both = live(tp, 5, {"area_x_ply": "0.01", "per_roll": "0.01"})
    one = live(tp, 6, {"area_x_ply": "0.01", "per_roll": "0.9"})
    flag_unit_price_outliers(NOW)
    assert held(both) == {"area_x_ply": "0.5", "per_roll": "1.0"}
    assert held(one) == {"area_x_ply": "0.5"}
    details = sorted(ReviewItem.objects.values_list("detail", flat=True))
    assert details == sorted(
        [
            f"outlier:area_x_ply:offer={both.pk}",
            f"outlier:per_roll:offer={both.pk}",
            f"outlier:area_x_ply:offer={one.pk}",
        ]
    )


def test_inactive_category_is_skipped_and_cleared(rice):
    four_normal(rice)
    low = live(rice, 6, {"per_mass": "0.0001"})
    flag_unit_price_outliers(NOW)
    Category.objects.filter(pk=rice.pk).update(active=False)
    stats = flag_unit_price_outliers(NOW)
    assert "pantry/rice" not in stats
    assert held(low) == {}


def test_flag_outliers_command_prints_counts_and_is_idempotent(
    rice, capsys, monkeypatch
):
    monkeypatch.setattr(
        "catalog.management.commands.flag_outliers.timezone.now", lambda: NOW
    )
    four_normal(rice)
    live(rice, 6, {"per_mass": "0.0001"})
    call_command("flag_outliers")
    first = capsys.readouterr().out
    assert "pantry/rice per_mass=1" in first
    call_command("flag_outliers")
    assert capsys.readouterr().out == first
    assert ReviewItem.objects.filter(reason="outlier", status="open").count() == 1
