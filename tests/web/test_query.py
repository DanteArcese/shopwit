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
