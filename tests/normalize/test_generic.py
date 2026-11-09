from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from normalize.generic import (
    GenericMeasure,
    generic_measure,
    generic_unit_price,
    is_personal,
    normalize_phrase,
    off_category_path,
    trigram_similarity,
)
from normalize.match import brand_tier_from_field
from normalize.quantity import parse_generic

A = pytest.approx


def _r(x):
    return A(x, rel=1e-3)


@pytest.mark.parametrize(
    "title,dim,amount",
    [
        ("Great Value 2% Milk, 64 oz", "oz_labeled", 64),
        ("Barilla Spaghetti, 16 oz", "oz_labeled", 16),
        ("Net Wt 16 oz", "oz_labeled", 16),
        ("64 fl oz", "volume", 1892.71),
        ("16 oz (454 g)", None, "more than one kind of size"),
        ("16.9 fl oz (500 g)", None, "more than one kind of size"),
        ("1 lb 2 oz", "mass", 510.29),
        ("6 x 16 oz", "oz_labeled", 96),
    ],
)
def test_bare_oz_own_group(title, dim, amount):
    m = generic_measure([title])
    assert m.dimension == dim
    if dim is None:
        assert m.reason == amount
    else:
        assert m.amount == _r(amount)


def test_first_deciding_input_wins():
    m = generic_measure(["", "Milk 1 gal", "2 L"])
    assert m.dimension == "volume" and m.amount == _r(3785.41)
    m = generic_measure(["12 ct", "Eggs 1 dozen"])
    assert m == GenericMeasure("count", 12.0, None)


def test_count_needs_no_size_unit():
    m = generic_measure(["Chobani Greek Yogurt, 5.3 oz, 4 Count"])
    assert (m.dimension, m.reason) == (None, "size unknown")
    assert generic_measure(["Bounty Paper Towels, 6 Double Rolls, 12 Count"]).dimension is None
    m = generic_measure(["Tide Pods, 81 Count"])
    assert (m.dimension, m.amount) == ("count", 81)
    assert generic_measure(["Tide Pods, 81 Pods"]).dimension is None


def test_structured_unit_only_for_off_input():
    m = generic_measure(["", "", "16 oz"], structured_unit="g")
    assert m.dimension == "mass" and m.amount == _r(453.59)
    m = generic_measure(["", "", "16 oz"], structured_unit="ml")
    assert m.dimension == "volume" and m.amount == _r(473.18)
    m = generic_measure(["16 oz"], structured_unit="g")
    assert (m.dimension, m.amount) == ("oz_labeled", 16)


def test_generic_unit_price():
    m = GenericMeasure("volume", 1892.70592, None)
    assert generic_unit_price(Decimal("3.00"), m) == Decimal("0.001585")
    assert generic_unit_price(Decimal("3.00"), GenericMeasure(None, None, "x")) is None


_FIXTURES = yaml.safe_load((Path(__file__).parent / "generic_fixtures.yaml").read_text())


def test_fixture_coverage():
    assert len(_FIXTURES) >= 30


@pytest.mark.parametrize("case", _FIXTURES, ids=lambda c: c["texts"][1] or c["texts"][2])
def test_generic_fixtures(case):
    m = generic_measure(case["texts"], case.get("structured_unit"))
    exp = case["expected"]
    if "reason" in exp:
        assert (m.dimension, m.reason) == (None, exp["reason"])
    else:
        assert m.dimension == exp["dimension"]
        assert m.amount == A(exp["amount"], rel=1e-2)


def test_parse_generic_flags_unparsed_unit():
    assert parse_generic("1-2 l")["has_size_unit"] == 1.0
    assert "volume_ml" not in parse_generic("1-2 l")


def test_normalize_phrase():
    assert normalize_phrase("  Oat   MILK ") == "oat milk"
    assert len(normalize_phrase("a" * 150)) == 100


def test_is_personal():
    assert is_personal("bob@example.com")
    assert is_personal("90210 milk")
    assert not is_personal("milk 2%")
    assert not is_personal("1234 milk")


@pytest.mark.parametrize(
    "phrase", ["555-123-4567", "(555) 123 4567", "4111 1111 1111 1111", "555.123.4567"]
)
def test_personal_phrase_with_separators(phrase):
    assert is_personal(phrase)


@pytest.mark.parametrize("phrase", ["2% milk 1 gallon", "16.9 fl oz 24 pack", "2 x 12 pack"])
def test_not_personal_phrases(phrase):
    assert not is_personal(phrase)


def test_off_category_path():
    assert (
        off_category_path(["en:dairies", "en:milks", "fr:laits"])
        == "Open Food Facts/Dairies/Milks"
    )
    assert off_category_path(["en:semi-skimmed-milks"]) == "Open Food Facts/Semi skimmed milks"
    assert off_category_path(["fr:laits"]) is None
    assert len(off_category_path(["en:" + "a" * 600])) == 500


def test_trigram_similarity():
    assert trigram_similarity("tolet paper", "toilet paper") >= 0.4
    assert trigram_similarity("milk", "milk") == 1.0
    assert trigram_similarity("", "") == 0.0
    assert trigram_similarity("xyz", "milk") < 0.4


def test_brand_tier_from_field():
    stores = frozenset({"great value"})
    assert brand_tier_from_field("Great Value", stores).value == "store_brand"
    assert brand_tier_from_field("Chobani", stores).value == "name_brand"
    assert brand_tier_from_field(None, stores) is None
    assert brand_tier_from_field("  ", stores) is None


def test_generic_loads_are_not_a_count():
    # Loads are not in the closed count-noun set, so generic search never reads them as
    # "count" (and never merges them with it): a loads-only title is size unknown, and a
    # title with a volume/mass uses that size. Pods/pacs counts follow the generic rules.
    m = generic_measure(["Tide Liquid Laundry Detergent, 64 Loads"])
    assert (m.dimension, m.reason) == (None, "size unknown")
    m = generic_measure(["Tide Liquid Laundry Detergent, 92 fl oz, 64 Loads"])
    assert (m.dimension, m.amount) == ("volume", _r(2720.76))
    m = generic_measure(["Tide Pods Laundry Detergent, 81 Count"])
    assert (m.dimension, m.amount) == ("count", 81)
