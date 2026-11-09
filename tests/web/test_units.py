from decimal import Decimal

import pytest

from catalog.units import display_size, display_unit_price, format_money, size_to_canonical


def shown(price, comparison, system, currency="USD"):
    value, label = display_unit_price(Decimal(price), comparison, system)
    return format_money(value, currency), label


def test_volume_imperial_and_metric():
    assert shown("0.002350", "per_volume", "imperial") == ("$0.0695", "fl oz")
    assert shown("0.002350", "per_volume", "metric") == ("$2.35", "L")


def test_mass():
    assert shown("0.010000", "per_mass", "imperial") == ("$0.283", "oz")
    assert shown("0.010000", "per_mass", "metric") == ("$10.00", "kg")


def test_count():
    for system in ("imperial", "metric"):
        assert shown("0.250000", "per_count", system) == ("$0.25", "each")


def test_area_imperial():
    assert shown("0.050000", "area_x_ply", "imperial") == ("$0.465", "100 sq ft·ply")
    assert shown("0.050000", "area_x_ply", "metric") == ("$0.05", "m²·ply")


def test_sheets_and_rolls_same_in_both_systems():
    for system in ("imperial", "metric"):
        assert shown("0.7", "per_100_sheets", system) == ("$0.70", "100 sheets")
        assert shown("1.5", "per_roll", system) == ("$1.50", "roll")


def test_unknown_combination_falls_back_to_canonical():
    assert display_unit_price(Decimal("0.5"), "per_volume", "martian") == (Decimal("0.5"), "mL")
    assert display_unit_price(Decimal("0.5"), "nonsense", "metric") == (Decimal("0.5"), "")


def test_never_zero_for_nonzero():
    assert format_money(Decimal("0.00123"), "USD") == "$0.00123"
    assert format_money(Decimal("0.0012345"), "USD") == "$0.00123"
    assert format_money(Decimal("0.000000123"), "USD") != "$0.00"
    assert format_money(Decimal("0.9996"), "USD") == "$1.00"
    assert format_money(Decimal("0.9995"), "USD") == "$1.00"  # half-up at the 3-digit boundary
    assert format_money(Decimal("0.99949"), "USD") == "$0.999"
    assert format_money(Decimal("9.995"), "USD") == "$10.00"  # half-up at the cent boundary
    assert format_money(Decimal("9.994"), "USD") == "$9.99"
    assert format_money(Decimal("0"), "USD") == "$0.00"


def test_format_money_two_decimals_over_a_dollar():
    assert format_money(Decimal("3.5"), "USD") == "$3.50"
    assert format_money(Decimal("1"), "USD") == "$1.00"
    assert format_money(Decimal("2.345"), "USD") == "$2.35"
    assert format_money(Decimal("1234.5"), "USD") == "$1,234.50"
    assert format_money(Decimal("0.5"), "USD") == "$0.50"
    assert format_money(Decimal("0.0695"), "USD") == "$0.0695"


def test_format_money_other_currency_and_negative():
    assert format_money(Decimal("3.5"), "CAD") == "CAD 3.50"
    with pytest.raises(ValueError):
        format_money(Decimal("-1"), "USD")


def test_size_to_canonical_round_trip():
    assert size_to_canonical(Decimal("64"), "volume", "imperial") == pytest.approx(1892.71, rel=1e-4)
    assert size_to_canonical(Decimal("2"), "volume", "metric") == 2000.0
    assert size_to_canonical(Decimal("16"), "mass", "imperial") == pytest.approx(453.592, rel=1e-4)
    assert size_to_canonical(Decimal("1.5"), "mass", "metric") == 1500.0
    assert size_to_canonical(Decimal("100"), "paper", "imperial") == pytest.approx(9.290304)
    assert size_to_canonical(Decimal("5"), "paper", "metric") == 5.0
    assert size_to_canonical(Decimal("12"), "count", "imperial") == 12.0
    # round trip through display
    ml = size_to_canonical(Decimal("64"), "volume", "imperial")
    assert display_size({"volume_ml": ml}, "volume", "imperial") == "64 fl oz"


@pytest.mark.parametrize(
    ("measures", "dimension", "system", "expected"),
    [
        ({"volume_ml": 1892.71}, "volume", "imperial", "64 fl oz"),
        ({"volume_ml": 3785.41}, "volume", "imperial", "128 fl oz (1 gal)"),
        ({"volume_ml": 7570.82}, "volume", "imperial", "256 fl oz (2 gal)"),
        ({"volume_ml": 1892.71}, "volume", "metric", "1.89 L"),
        ({"volume_ml": 946.35}, "volume", "metric", "946.35 mL"),
        ({"volume_ml": 500}, "volume", "metric", "500 mL"),
        ({"volume_ml": 2000}, "volume", "metric", "2 L"),
        ({"volume_ml": 20000}, "volume", "metric", "20 L"),
        ({"mass_g": 226.796}, "mass", "imperial", "8 oz"),
        ({"mass_g": 453.592}, "mass", "imperial", "16 oz (1 lb)"),
        ({"mass_g": 1360.78}, "mass", "imperial", "48 oz (3 lb)"),
        ({"mass_g": 500}, "mass", "metric", "500 g"),
        ({"mass_g": 1500}, "mass", "metric", "1.5 kg"),
        ({"count": 12}, "count", "imperial", "12 ct"),
        ({"count": 1200}, "count", "metric", "1,200 ct"),
        ({"rolls": 18, "sheets": 4356, "area_m2": 44.07}, "paper", "imperial", "18 rolls · 4,356 sheets"),
        ({"rolls": 1, "sheets": 100}, "paper", "metric", "1 roll · 100 sheets"),
        ({"rolls": 6}, "paper", "metric", "6 rolls"),
        ({"sheets": 200}, "paper", "metric", "200 sheets"),
        ({"area_m2": 10.0}, "paper", "metric", ""),
        ({}, "volume", "metric", ""),
        ({"mass_g": 5}, "volume", "metric", ""),
    ],
)
def test_display_size_variants(measures, dimension, system, expected):
    assert display_size(measures, dimension, system) == expected


def test_generic_display():
    from catalog.units import display_generic_size

    assert display_unit_price(Decimal("0.05"), "per_oz_labeled", "metric") == (
        Decimal("0.05"),
        "oz (as labeled)",
    )
    assert display_generic_size("oz_labeled", 64.0, "metric") == "64 oz"
    assert display_generic_size("volume", 1892.70592, "imperial").startswith("64 fl oz")


def test_display_per_load():
    assert display_unit_price(Decimal("0.2"), "per_load", "imperial") == (Decimal("0.2"), "load")
    assert display_unit_price(Decimal("0.2"), "per_load", "metric") == (Decimal("0.2"), "load")
    m = {"loads": 64, "volume_ml": 2720.76}
    assert display_size(m, "laundry", "imperial") == "64 loads · 92 fl oz"
    assert display_size(m, "laundry", "metric") == "64 loads · 2.72 L"
    assert display_size({"loads": 1, "mass_g": 1360.78}, "laundry", "imperial") == "1 load · 48 oz (3 lb)"
    assert display_size({"count": 81}, "laundry", "metric") == "81 ct"
    assert display_size({}, "laundry", "metric") == ""


def test_display_tissue():
    assert display_size({"boxes": 8, "sheets": 960}, "tissue", "imperial") == "8 boxes · 960 sheets"
    assert display_size({"sheets": 144}, "tissue", "metric") == "144 sheets"
    assert display_size({"boxes": 1, "sheets": 144}, "tissue", "metric") == "1 box · 144 sheets"
    assert display_size({}, "tissue", "metric") == ""
    assert display_unit_price(Decimal("0.5"), "sheets_x_ply", "metric") == (Decimal("0.5"), "100 sheets·ply")
    assert display_unit_price(Decimal("0.5"), "sheets_x_ply", "imperial") == (Decimal("0.5"), "100 sheets·ply")


def test_tissue_size_unit_ignores_system():
    from web.views import _size_system

    assert _size_system("tissue", "sheets", "metric") == "metric"
    assert _size_system("tissue", "sheets", "imperial") == "imperial"
