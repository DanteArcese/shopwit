from datetime import date
from decimal import Decimal

import pytest

from catalog.units import DISPLAY
from web.seo import (
    PriceFact,
    category_prices_description,
    category_title,
    median_decimal,
    option_label,
    summarize_prices,
    unit_word,
)


@pytest.mark.parametrize(
    ("label", "word"),
    [
        ("oz", "ounce"),
        ("fl oz", "fluid ounce"),
        ("kg", "kg"),
        ("L", "litre"),
        ("each", "item"),
        ("roll", "roll"),
        ("load", "load"),
        ("bag", "bag"),
        ("100 sheets", "100 sheets"),
        ("100 sheets·ply", "100 sheets·ply"),
        ("100 sq ft·ply", "100 sq ft·ply"),
        ("m²·ply", "m²·ply"),
        ("oz (as labeled)", "oz (as labeled)"),
        ("", ""),
        ("parsec", ""),
    ],
)
def test_unit_word(label, word):
    assert unit_word(label) == word


def test_every_display_label_has_a_unit_word():
    assert all(unit_word(label) for label, _ in DISPLAY.values())


def test_category_title():
    assert category_title("Rice", "oz") == "Rice price per ounce: compare stores | UnitPricer"
    assert category_title("Milk", "L") == "Milk price per litre: compare stores | UnitPricer"
    assert category_title("Eggs", "each") == "Eggs price per item: compare stores | UnitPricer"
    assert category_title("Rice", "") == "Rice: compare price per unit | UnitPricer"
    assert category_title("Rice", "parsec") == "Rice: compare price per unit | UnitPricer"


def fact(price, store="Kroger", seen=date(2026, 11, 3), currency="USD"):
    return PriceFact(Decimal(price) if price is not None else None, currency, store, seen)


def test_median_decimal():
    assert median_decimal([Decimal("3"), Decimal("1"), Decimal("2")]) == Decimal("2")
    assert median_decimal([Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]) == Decimal("2.5")
    assert median_decimal([]) is None


def test_summarize_none_without_facts():
    assert summarize_prices([]) is None


def test_summarize_numbers():
    s = summarize_prices([
        fact("0.30", "Kroger", date(2026, 11, 1)),
        fact("0.10", "Walmart", date(2026, 11, 3)),
        fact("0.20", "Kroger", date(2026, 10, 20)),
        fact(None, "Target", date(2026, 11, 2)),  # unranked: counts, never priced
    ])
    assert (s.count, s.stores) == (4, 3)
    assert (s.lowest.price, s.lowest.store, s.lowest.seen) == (Decimal("0.10"), "Walmart", date(2026, 11, 3))
    assert s.median == Decimal("0.20")  # of the three priced rows
    assert s.latest == date(2026, 11, 3)


def test_summarize_median_needs_three_priced_rows():
    s = summarize_prices([fact("0.10"), fact("0.20")])
    assert s.median is None
    assert summarize_prices([fact("0.10"), fact("0.20"), fact("0.30")]).median == Decimal("0.20")


def test_summarize_no_priced_rows_has_no_lowest():
    s = summarize_prices([fact(None)])
    assert s.lowest is None and s.median is None


def test_description_plural_and_singular():
    s = summarize_prices([fact("0.10", "Walmart", date(2026, 11, 3)), fact("0.30", "Kroger")])
    assert category_prices_description("oz", s) == (
        "2 prices from 2 stores. Lowest seen: $0.10/oz at Walmart (Nov 3, 2026). "
        "Compared by price per ounce."
    )
    one = summarize_prices([fact("1.25", "Walmart", date(2026, 11, 3))])
    assert category_prices_description("kg", one) == (
        "1 price from 1 store. Lowest seen: $1.25/kg at Walmart (Nov 3, 2026). "
        "Compared by price per kg."
    )


def test_description_without_a_lowest_price():
    s = summarize_prices([fact(None)])
    assert category_prices_description("oz", s) == "1 price from 1 store. Compared by price per ounce."


def test_description_blank_unit():
    s = summarize_prices([fact("1")])
    assert category_prices_description("", s).endswith("Compared by price per unit.")


@pytest.mark.parametrize(
    ("code", "label"),
    [("long_grain", "Long grain"), ("arborio", "Arborio"), (3, "3"), ("a2", "A2"), ("2%", "2%")],
)
def test_option_label(code, label):
    assert option_label(code) == label
