from pathlib import Path

import pytest

from normalize import load_categories
from normalize.query import QueryResolution, resolve_query

CATS = load_categories(Path("categories"))


def r(text):
    return resolve_query(text, CATS)


def test_milk_no_filters():
    assert r("milk") == QueryResolution("dairy/milk", {}, "")


def test_two_percent():
    assert r("2% milk").filters == {"fat": "2"}


def test_organic_two_percent():
    res = r("organic 2% milk")
    assert res.category == "dairy/milk"
    assert res.filters == {"fat": "2", "organic": True}
    assert res.leftover == ""


def test_reduced_fat():
    assert r("reduced fat milk").filters == {"fat": "2"}


def test_two_ply_toilet_paper():
    res = r("2 ply toilet paper")
    assert res.category == "household/toilet-paper"
    assert res.filters == {"ply": 2}
    assert isinstance(res.filters["ply"], int)


@pytest.mark.parametrize("text", ["large eggs", "large egg"])
def test_large_eggs(text):
    res = r(text)
    assert res.category == "dairy/eggs"
    assert res.filters == {"size": "large"}
    assert res.leftover == ""


def test_unmatched():
    assert r("garden hose") == QueryResolution(None, {}, "garden hose")


def test_ambiguous_filter_dropped():
    res = r("1% 2% milk")
    assert res.category == "dairy/milk"
    assert "fat" not in res.filters


def test_leftover():
    assert r("Kirkland 2% milk").leftover == "kirkland"


def test_absent_defaults_not_filters():
    assert r("milk").filters == {}


def test_empty():
    assert r("") == QueryResolution(None, {}, "")
    assert r("   ") == QueryResolution(None, {}, "")


@pytest.mark.parametrize(
    "text", ["chocolate milk", "almond milk", "oat milk", "soy milk", "coconut milk",
             "egg noodles", "rice milk", "liquid egg", "toilet paper wipes"],
)
def test_excluded_queries_not_rewritten(text):
    res = r(text)
    assert res.category not in ("dairy/milk", "dairy/eggs", "household/toilet-paper")


def test_paper_towels_plural_both_ways():
    assert r("paper towel").category == r("paper towels").category
    assert r("paper towel").category is not None


def test_leftover_never_mutated():
    assert r("chocolates milk").leftover == "chocolates"
    assert r("kirkland large eggs").leftover == "kirkland"


def test_filter_after_and_case():
    assert r("milk 2%").filters == {"fat": "2"}
    res = r("MILK")
    assert res == QueryResolution("dairy/milk", {}, "")


def test_punctuation_only_leftover_empty():
    assert r("(2% milk") == QueryResolution("dairy/milk", {"fat": "2"}, "")


@pytest.mark.parametrize(
    "text, category, filters",
    [
        ("unsalted butter", "dairy/butter", {"salted": "unsalted"}),
        ("greek yogurt", "dairy/yogurt", {"greek": True}),
        ("shredded cheddar", "dairy/cheese", {"form": "shredded", "variety": "cheddar"}),
    ],
)
def test_dairy_extras(text, category, filters):
    res = r(text)
    assert res.category == category
    assert res.filters == filters


def test_peanut_butter_not_butter():
    assert r("peanut butter").category == "pantry/peanut-butter"


def test_whole_milk_yogurt_is_yogurt_not_milk():
    assert r("whole milk yogurt").category == "dairy/yogurt"


def test_bread_flour_is_flour():
    assert r("bread flour") == QueryResolution("pantry/flour", {"type": "bread"}, "")


@pytest.mark.parametrize(
    "text, category, filters",
    [
        ("whole wheat bread", "pantry/bread", {"grain": "whole_wheat"}),
        ("jasmine rice", "pantry/rice", {"variety": "jasmine"}),
        ("brown rice", "pantry/rice", {"brown": True}),
        ("egg noodles", "pantry/pasta", {"shape": "egg_noodles"}),
    ],
)
def test_grains(text, category, filters):
    res = r(text)
    assert res.category == category
    assert res.filters == filters


def test_rice_milk_not_rewritten():
    assert r("rice milk").category is None


@pytest.mark.parametrize(
    "text, category, filters",
    [
        ("olive oil", "pantry/cooking-oil", {"type": "olive"}),
        ("extra virgin olive oil", "pantry/cooking-oil", {"type": "extra_virgin_olive"}),
        ("brown sugar", "pantry/sugar", {"type": "brown"}),
        ("whey protein isolate", "pantry/protein-powder", {"type": "whey_isolate"}),
        ("peanut butter", "pantry/peanut-butter", {}),
    ],
)
def test_pantry_staples(text, category, filters):
    res = r(text)
    assert res.category == category
    assert res.filters == filters


@pytest.mark.parametrize(
    "text, category, filters",
    [
        ("club soda", "beverages/sparkling-water", {"unflavored": True}),
        ("diet soda", "beverages/soda", {"diet": True}),
        ("spring water", "beverages/bottled-water", {"water_type": "spring"}),
        (
            "orange juice not from concentrate",
            "beverages/orange-juice",
            {"concentrate": "not_from_concentrate"},
        ),
    ],
)
def test_beverages(text, category, filters):
    res = r(text)
    assert res.category == category
    assert res.filters == filters


def test_baking_soda_unmatched():
    assert r("baking soda").category is None


@pytest.mark.parametrize(
    "text, category, filters",
    [
        ("3 ply facial tissue", "household/facial-tissue", {"ply": 3}),
        ("13 gallon trash bags", "household/trash-bags", {"capacity_gal": 13}),
    ],
)
def test_household_paper_and_bags(text, category, filters):
    res = r(text)
    assert res.category == category
    assert res.filters == filters
    assert r("paper towels").category == "household/paper-towels"
