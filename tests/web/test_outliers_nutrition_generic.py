"""A held size (#127) also blocks nutrition ranking and generic search ranking."""

from decimal import Decimal

import pytest

from catalog.browse import FilterSpec, browse
from catalog.generic import group_products
from catalog.models import Category, Offer
from catalog.outliers import HELD_REASON, approve
from tests.catalog.helpers import NOW
from tests.catalog.test_generic_match import make_generic_offer, nutrify_offer
from tests.web.test_browse_outliers import chips, live

NONE = FilterSpec(attributes={})


@pytest.fixture
def rice(milk):
    return Category.objects.get(slug="pantry/rice")


def protein(offer, cost, unit="g"):
    Offer.objects.filter(pk=offer.pk).update(
        nutrition={
            "pack_unit": unit,
            "protein_cost": cost,
            "kcal_cost": cost,
            "protein_reason": None,
            "kcal_reason": None,
        }
    )


def seen_rows(rice):
    """Four normal observed rice offers costed by protein, plus the mis-sized chips."""
    normal = []
    for n in range(1, 5):
        o = chips(rice, n)
        Offer.objects.filter(pk=o.pk).update(
            unit_prices={"per_mass": "0.01"}, price=Decimal("3.00")
        )
        protein(o, "0.50")
        normal.append(o)
    bad = chips(rice, 9)
    protein(bad, "0.01")  # 10x too cheap per gram of protein: same wrong size
    Offer.objects.filter(pk=bad.pk).update(held_comparisons={"per_mass": "0.01"})
    return normal, bad


@pytest.mark.parametrize("rank", ["protein", "kcal"])
def test_held_offer_does_not_top_nutrition_ranking(rice, rank):
    normal, bad = seen_rows(rice)
    res = browse(rice, NONE, None, None, NOW, observed=True, rank=rank)
    assert bad not in [r.offer for r in res.ranked]
    [row] = [r for r in res.unranked if r.offer == bad]
    assert row.reason == HELD_REASON and row.cost is None


def test_approved_offer_tops_nutrition_ranking(rice):
    _, bad = seen_rows(rice)
    approve(bad, "per_mass")
    res = browse(rice, NONE, None, None, NOW, observed=True, rank="protein")
    assert res.ranked[0].offer == bad


def test_other_dimension_hold_keeps_nutrition_cost(rice):
    _, bad = seen_rows(rice)
    protein(
        bad, "0.01", unit="ml"
    )  # costed per ml: a held per_mass size is not what it uses
    res = browse(rice, NONE, None, None, NOW, observed=True, rank="protein")
    assert res.ranked[0].offer == bad


# --- generic search ----------------------------------------------------------------


def generic_chips(db):
    a = make_generic_offer("Cacao Baking Chips A", "4.00", dim="mass", amount=300)
    b = make_generic_offer("Cacao Baking Chips B", "5.00", dim="mass", amount=300)
    bad = make_generic_offer(
        "Cacao Baking Chips 115 oz", "8.99", dim="mass", amount=3260
    )
    Offer.objects.filter(pk=bad.pk).update(held_comparisons={"per_mass": "0.01"})
    return a, b, Offer.objects.get(pk=bad.pk)


def test_held_offer_does_not_rank_in_generic_groups(db):
    a, b, bad = generic_chips(db)
    [group] = group_products(Offer.objects.all()).groups
    assert [r.offer for r in group.rows[: group.ranked_count]] == [a, b]
    [row] = group.rows[group.ranked_count :]
    assert row.offer == bad and row.unit_price is None and row.reason == HELD_REASON


def test_held_offer_not_top_of_text_search(client, db, frozen_now):
    generic_chips(db)
    page = client.get("/search", {"q": "cacao baking chips"}).content.decode()
    assert (
        page.index("Cacao Baking Chips A")
        < page.index("Can't rank")
        < page.index("115 oz")
    )
    assert HELD_REASON in page


def test_approved_offer_tops_text_search(client, db, frozen_now):
    _, _, bad = generic_chips(db)
    approve(bad, "per_mass")
    page = client.get("/search", {"q": "cacao baking chips"}).content.decode()
    assert page.index("115 oz") < page.index("Cacao Baking Chips A")
    assert HELD_REASON not in page


def test_held_offer_does_not_top_generic_protein_ranking(db):
    a, b, bad = generic_chips(db)
    for o in (a, b, bad):
        nutrify_offer(o)
    bad.refresh_from_db()
    a.refresh_from_db()
    assert Decimal(bad.nutrition["protein_cost"]) < Decimal(a.nutrition["protein_cost"])
    [group] = group_products(Offer.objects.all(), "protein").groups
    assert group.rows[0].offer == a
    [row] = [r for r in group.rows if r.offer == bad]
    assert row.cost is None and row.reason == HELD_REASON


def test_rank_select_ignores_held_costs(rice):
    from catalog.browse import nutrition_rankable

    bad = chips(rice, 9)
    protein(bad, "0.01")
    assert nutrition_rankable(rice, None, None, NOW)
    Offer.objects.filter(pk=bad.pk).update(held_comparisons={"per_mass": "0.01"})
    assert not nutrition_rankable(rice, None, None, NOW)
