from datetime import timedelta
from decimal import Decimal

import pytest

from catalog.generic import (
    MATCH_CAP,
    SIZES_DISAGREE,
    group_products,
    match_offers,
)
from catalog.models import Offer, Product
from normalize.nutrition import offer_nutrition
from normalize.types import Nutrition
from tests.catalog.helpers import NOW


def make_generic_offer(
    title, price, *, dim, amount, retailer="fake", brand=None, path=None, labels=(), kind="live",
    age_hours=1, product=None,
):
    if product is None:
        product, _ = Product.objects.get_or_create(
            identity=f"{retailer}:{title}", defaults={"title": title, "brand": brand}
        )
    unit = (Decimal(price) / Decimal(str(amount))).quantize(Decimal("0.000001")) if dim else None
    extra = {}
    if kind == "observed":
        extra = {"observed_on": (NOW - timedelta(hours=age_hours)).date()}
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=f"{product.identity}:{retailer}:{title}",
        price=Decimal(price), currency="USD", url="https://example.com/", title=title, brand=brand,
        category_path=path, labels=list(labels), kind=kind,
        last_seen_at=NOW - timedelta(hours=age_hours),
        measure_dim=dim, measure_amount=amount if dim else None, generic_unit_price=unit, **extra,
    )


def nutrify_offer(offer, basis="100g", protein=10.0, kcal=100.0):
    offer.nutrition = offer_nutrition(
        offer.price, Nutrition(basis, protein, kcal), offer.measures, offer.measure_dim, offer.measure_amount
    )
    offer.save(update_fields=["nutrition"])
    return offer


def titles(match):
    return {o.title for o in match.offers}


@pytest.mark.django_db
def test_text_match_all_terms():
    make_generic_offer("Oatly Oat Milk", "4", dim="volume", amount=1000, brand="Oatly", path="Food/Dairy alternatives")
    make_generic_offer("Whole Milk", "3", dim="volume", amount=1000)
    assert titles(match_offers("oat milk", NOW)) == {"Oatly Oat Milk"}
    assert titles(match_offers("oatly", NOW)) == {"Oatly Oat Milk"}
    assert titles(match_offers("dairy alternatives", NOW)) == {"Oatly Oat Milk"}


@pytest.mark.django_db
def test_trigram_fallback_marks_fuzzy():
    make_generic_offer("Charmin Toilet Paper", "9", dim="count", amount=12)
    m = match_offers("tolet paper", NOW)
    assert titles(m) == {"Charmin Toilet Paper"} and m.fuzzy
    for i in range(6):
        make_generic_offer(f"Milk {i}", "3", dim="volume", amount=1000)
    assert not match_offers("milk", NOW).fuzzy


@pytest.mark.django_db
def test_cap_500():
    products = [Product(identity=f"p{i}", title=f"Milk {i}") for i in range(MATCH_CAP + 10)]
    Product.objects.bulk_create(products)
    Offer.objects.bulk_create(
        [
            Offer(product=p, retailer="fake", retailer_item_id=p.identity, price=Decimal("1"),
                  currency="USD", url="https://example.com/", title=p.title, last_seen_at=NOW)
            for p in Product.objects.all()
        ]
    )
    m = match_offers("milk", NOW)
    assert len({o.product_id for o in m.offers}) == MATCH_CAP and m.capped
    assert not match_offers("milk", NOW, stores=frozenset({"none"})).capped


@pytest.mark.django_db
def test_freshness_and_stores():
    make_generic_offer("Old Milk", "3", dim="volume", amount=1000, retailer="walmart", age_hours=25)
    make_generic_offer("Fresh Milk", "3", dim="volume", amount=1000)
    make_generic_offer("Seen Milk", "3", dim="volume", amount=1000, retailer="shop", kind="observed")
    assert titles(match_offers("milk", NOW)) == {"Fresh Milk"}
    assert titles(match_offers("milk", NOW, stores=frozenset({"other"}))) == set()
    assert titles(match_offers("milk", NOW, observed=True)) == {"Seen Milk"}


@pytest.mark.django_db
def test_group_order_and_ranking():
    for i, p in enumerate(["5", "2", "2", ]):
        make_generic_offer(f"Vol {'cba'[i]}", p, dim="volume", amount=1000)
    make_generic_offer("Mass a", "4", dim="mass", amount=500)
    make_generic_offer("Mass b", "5", dim="mass", amount=500)
    make_generic_offer("Count a", "4", dim="count", amount=12)
    res = group_products(Offer.objects.select_related("product", "store"))
    assert [g.dimension for g in res.groups] == ["volume", "mass", "count"]
    assert [r.product.title for r in res.groups[0].rows] == ["Vol a", "Vol b", "Vol c"]
    assert res.total_products == 6


@pytest.mark.django_db
def test_cheapest_offer_per_product():
    p = Product.objects.create(identity="u1", title="Milk")
    make_generic_offer("Milk", "5", dim="volume", amount=1000, retailer="a", product=p)
    cheap = make_generic_offer("Milk", "3", dim="volume", amount=1000, retailer="b", product=p)
    res = group_products(Offer.objects.select_related("product", "store"))
    assert len(res.groups[0].rows) == 1 and res.groups[0].rows[0].offer == cheap


@pytest.mark.django_db
def test_product_with_conflicting_dimensions_is_unknown():
    p = Product.objects.create(identity="u1", title="Milk")
    make_generic_offer("Milk", "5", dim="oz_labeled", amount=64, retailer="a", product=p)
    make_generic_offer("Milk", "3", dim="volume", amount=1893, retailer="b", product=p)
    res = group_products(Offer.objects.select_related("product", "store"))
    assert res.groups == [] and len(res.unknown) == 1
    assert res.unknown[0].reason == SIZES_DISAGREE and res.unknown[0].unit_price is None
    assert res.unknown[0].offer.retailer == "b"


@pytest.mark.django_db
def test_unknown_size_uses_offer_reason():
    o = make_generic_offer("Mystery", "3", dim=None, amount=None)
    o.measure_reason = "no size in title"
    o.save()
    res = group_products(Offer.objects.select_related("product", "store"))
    assert res.unknown[0].reason == "no size in title"


@pytest.mark.django_db
@pytest.mark.parametrize("q", ["the", "!!!", '"milk', "-", "a", "🥛", "x" * 200])
def test_degenerate_queries_never_error(q):
    make_generic_offer("Milk", "3", dim="volume", amount=1000)
    assert match_offers(q, NOW).offers is not None


@pytest.mark.django_db
def test_match_query_count_fixed(django_assert_num_queries):
    for i in range(6):
        make_generic_offer(f"Milk {i}", "3", dim="volume", amount=1000)
    with django_assert_num_queries(2):
        match_offers("milk", NOW)
    for i in range(6, 60):
        make_generic_offer(f"Milk {i}", "3", dim="volume", amount=1000)
    with django_assert_num_queries(2):
        match_offers("milk", NOW)


@pytest.mark.django_db
def test_fuzzy_query_count_is_three(django_assert_num_queries):
    make_generic_offer("Charmin Toilet Paper", "9", dim="count", amount=12)
    with django_assert_num_queries(3):
        assert match_offers("tolet paper", NOW).fuzzy


@pytest.mark.django_db
def test_bench_search_runs_and_cleans_up(settings):
    from io import StringIO

    from django.core.management import CommandError, call_command

    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("bench_search", offers=10)
    settings.DEBUG = True
    out = StringIO()
    call_command("bench_search", offers=300, stdout=out)
    assert "p50=" in out.getvalue() and "p95=" in out.getvalue()
    assert not Product.objects.filter(identity__startswith="bench:").exists()


@pytest.mark.django_db
def test_group_sort_is_none_safe_for_missing_unit_price():
    make_generic_offer("Priced Milk", "3", dim="volume", amount=1000)
    bad = make_generic_offer("Unpriced Milk", "1", dim="volume", amount=1000)
    Offer.objects.filter(pk=bad.pk).update(generic_unit_price=None)
    result = group_products(Offer.objects.select_related("product").all())
    assert [r.product.title for r in result.groups[0].rows] == ["Priced Milk", "Unpriced Milk"]


@pytest.mark.django_db
def test_bench_search_cleans_up_when_seeding_is_interrupted(settings):
    from django.core.management import call_command

    from catalog.management.commands import bench_search

    class Boom:
        def __init__(self, **kwargs):
            pass

        class objects:
            @staticmethod
            def bulk_create(*args, **kwargs):
                raise RuntimeError("interrupted")

    settings.DEBUG = True
    real = bench_search.Offer
    bench_search.Offer = Boom
    try:
        with pytest.raises(RuntimeError):
            call_command("bench_search", offers=20)
    finally:
        bench_search.Offer = real
    assert not Product.objects.filter(identity__startswith="bench:").exists()


@pytest.mark.django_db
def test_title_match_outranks_category_path_match_when_capped():
    """The stored vector's weights count: a title (A) hit beats a category-path (C) hit."""
    products = [Product(identity=f"p{i}", title=f"Thing {i}") for i in range(MATCH_CAP)]
    Product.objects.bulk_create(products)
    Offer.objects.bulk_create(
        [
            Offer(product=p, retailer="fake", retailer_item_id=p.identity, price=Decimal("1"),
                  currency="USD", url="https://example.com/", title=p.title, last_seen_at=NOW,
                  category_path="Food/Dairies/Milks")
            for p in Product.objects.all()
        ]
    )
    titled = make_generic_offer("Fresh Milk", "3", dim="volume", amount=1000)
    m = match_offers("milk", NOW)
    assert m.capped
    assert titled.product_id in {o.product_id for o in m.offers}


@pytest.mark.django_db
def test_match_defers_heavy_columns():
    make_generic_offer("Oat Milk", "3", dim="volume", amount=1000)
    offer = match_offers("oat milk", NOW).offers[0]
    assert {"search_vector", "unit_prices", "unresolved", "attributes"} <= offer.get_deferred_fields()
    assert {"attributes", "manual_attributes", "measures"} <= offer.product.get_deferred_fields()
    assert "measures" not in offer.get_deferred_fields()  # rows display it


def _all():
    return Offer.objects.select_related("product", "store")


@pytest.mark.django_db
def test_group_rank_protein_costed_first_then_unranked():
    # protein cost = price * 10000 / (protein * 500): A 4.00, B 2.00 (best), C costless, D none
    a = nutrify_offer(make_generic_offer("A", "4", dim="mass", amount=500), protein=20.0)
    b = nutrify_offer(make_generic_offer("B", "5", dim="mass", amount=500), protein=50.0)
    make_generic_offer("C", "1", dim="mass", amount=500)  # no nutrition at all: cheapest, still last
    nutrify_offer(make_generic_offer("D", "2", dim="mass", amount=500), basis="serving")
    group = group_products(_all(), rank="protein").groups[0]
    assert [r.product.title for r in group.rows] == ["B", "A", "C", "D"]
    assert group.ranked_count == 2 and group.total == 4
    assert [r.cost for r in group.rows] == [Decimal("2.00"), Decimal("4.00"), None, None]
    assert [r.reason for r in group.rows] == [
        None, None, "no nutrition data from Open Food Facts", "nutrition per serving only",
    ]
    assert group.rows[0].offer == b and group.rows[1].offer == a


@pytest.mark.django_db
def test_group_rank_never_mixes_dimensions():
    nutrify_offer(make_generic_offer("Mass", "4", dim="mass", amount=500), protein=20.0)
    nutrify_offer(make_generic_offer("Vol", "9", dim="volume", amount=1000), basis="100ml", protein=1.0)
    result = group_products(_all(), rank="protein")
    by = {g.dimension: g for g in result.groups}
    assert [r.product.title for r in by["mass"].rows] == ["Mass"]
    assert [r.product.title for r in by["volume"].rows] == ["Vol"]
    assert by["mass"].rows[0].cost == Decimal("4.00") and by["volume"].rows[0].cost == Decimal("90.00")


@pytest.mark.django_db
def test_group_rank_picks_the_offer_with_the_lowest_cost():
    p = Product.objects.create(identity="u1", title="Milk")
    make_generic_offer("Milk", "3", dim="mass", amount=500, retailer="a", product=p)  # cheapest, no cost
    costed = nutrify_offer(
        make_generic_offer("Milk", "4", dim="mass", amount=500, retailer="b", product=p), protein=20.0
    )
    row = group_products(_all(), rank="protein").groups[0].rows[0]
    assert row.offer == costed and row.cost == Decimal("4.00")


@pytest.mark.django_db
def test_group_rank_unit_unchanged():
    for t, price in (("Vol c", "5"), ("Vol a", "2"), ("Vol b", "2")):
        nutrify_offer(make_generic_offer(t, price, dim="volume", amount=1000), basis="100ml")
    for rank in ("unit", "bogus"):
        group = group_products(_all(), rank=rank).groups[0]
        assert [r.product.title for r in group.rows] == ["Vol a", "Vol b", "Vol c"]
        assert group.ranked_count == 3 and all(r.cost is None for r in group.rows)
    assert [r.product.title for r in group_products(_all()).groups[0].rows] == ["Vol a", "Vol b", "Vol c"]


@pytest.mark.django_db
def test_group_rank_count_and_oz_labeled_all_unranked():
    nutrify_offer(make_generic_offer("Eggs", "4", dim="count", amount=12))
    nutrify_offer(make_generic_offer("Milk oz", "3", dim="oz_labeled", amount=64))
    result = group_products(_all(), rank="protein")
    assert [g.dimension for g in result.groups] == ["oz_labeled", "count"]
    for g in result.groups:
        assert g.ranked_count == 0
        assert [r.reason for r in g.rows] == ["pack size unknown"] and g.rows[0].cost is None


@pytest.mark.django_db
def test_group_rank_uncosted_reason_prefers_an_offer_with_nutrition_data():
    p = Product.objects.create(identity="u2", title="Water")
    cheapest = make_generic_offer("Water", "1", dim="mass", amount=500, retailer="a", product=p)  # no data
    nutrify_offer(make_generic_offer("Water", "3", dim="mass", amount=500, retailer="c", product=p), basis="serving")
    nutrify_offer(make_generic_offer("Water", "2", dim="mass", amount=500, retailer="b", product=p), protein=0.0)
    row = group_products(_all(), rank="protein").groups[0].rows[0]
    assert row.offer == cheapest and row.cost is None
    assert row.reason == "no protein"  # the cheapest offer with data, not "no nutrition data"
