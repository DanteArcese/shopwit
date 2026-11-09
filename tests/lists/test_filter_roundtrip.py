import pytest
from django.http import QueryDict

from catalog.browse import (
    FilterSpec,
    browse,
    matching_offers,
    parse_filters,
    parse_filters_strict,
    spec_params,
)
from catalog.generic import generic_matching_offers, group_products, match_offers
from tests.catalog.helpers import NOW, raw
from tests.catalog.test_generic_match import make_generic_offer


def test_spec_params_round_trip(milk, tp):
    p = {"fat": "2", "organic": "true", "brand_tier": "store_brand", "size_min": "946.353"}
    spec = parse_filters(milk, p)
    assert spec.attributes and spec.size_min == 946.353
    assert parse_filters(milk, spec_params(spec)) == spec
    assert spec_params(parse_filters(tp, {"ply": "2"})) == {"ply": "2"}
    assert parse_filters(tp, spec_params(parse_filters(tp, {"ply": "2"}))) == parse_filters(tp, {"ply": "2"})
    assert spec_params(FilterSpec(attributes={})) == {}


def test_strict_reports_invalid(milk):
    spec, invalid = parse_filters_strict(milk, {"fat": "banana", "colour": "red", "organic": "true"})
    assert spec.attributes == {"organic": True}
    assert invalid == ("colour", "fat")


def test_strict_bad_tier_and_size(milk):
    spec, invalid = parse_filters_strict(milk, {"brand_tier": "x", "size_min": "abc", "size_max": "5"})
    assert invalid == ("brand_tier", "size_min")
    assert spec.size_max == 5.0


def test_strict_any_is_not_invalid(milk):
    spec, invalid = parse_filters_strict(milk, {"fat": "any"})
    assert not spec.attributes and invalid == ()


def test_matching_offers_equals_browse_population(milk, load):
    load(milk, [
        raw(title="Acme Organic 2% Milk, 1 L", price="2.00", item="a", brand="Acme"),
        raw(title="Bravo Whole Milk, 1 L", price="1.00", item="b", brand="Bravo"),
        raw(title="Cargo Milk, 1 L", price="1.50", item="c", brand="Cargo"),
        raw(title="Mystery Whole Milk", price="3.00", item="d", brand="Mystery"),
    ])
    for params in ({}, {"fat": "whole"}, {"size_min": "900"}, {"organic": "true"}):
        spec = parse_filters(milk, params)
        res = browse(milk, spec, None, None, NOW, limit=100)
        expected = {r.product.id for r in res.ranked + res.unranked}
        got = matching_offers(milk, spec, None, NOW)
        assert {o.product_id for o in got} == expected
    assert matching_offers(milk, FilterSpec(attributes={}), frozenset({"nobody"}), NOW) == []


@pytest.mark.django_db
def test_generic_matching_equals_group_population():
    make_generic_offer("Fizz Soda A", "2", dim="volume", amount=1000)
    make_generic_offer("Fizz Soda B", "3", dim="volume", amount=2000)
    make_generic_offer("Fizz Soda Powder", "4", dim="mass", amount=500)
    make_generic_offer("Fizz Soda Cans", "5", dim="count", amount=12)
    for dim in ("volume", "mass", "count"):
        group = {
            r.product.id
            for g in group_products(match_offers("fizz soda", NOW).offers).groups
            if g.dimension == dim
            for r in g.rows
        }
        got = generic_matching_offers("fizz soda", {}, dim, None, NOW)
        assert {o.product_id for o in got} == group and group
    got = generic_matching_offers("fizz soda", {"vol_min": ["1.5"]}, "volume", None, NOW)
    assert [o.title for o in got] == ["Fizz Soda B"]
