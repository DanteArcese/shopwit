from decimal import Decimal

from normalize.categories import CategoryDef, KNOWN_COMPARISONS
from normalize.compare import COMPARISONS, unit_prices
from normalize.types import AttrValue


def cat(default, alternates=(), dimension="volume"):
    return CategoryDef(
        slug="x",
        name="X",
        parent=None,
        include=(),
        exclude=(),
        dimension=dimension,
        attributes={},
        comparison_default=default,
        comparison_alternates=tuple(alternates),
    )


def test_compare_keys_match_known():
    assert set(COMPARISONS) == KNOWN_COMPARISONS


def test_per_volume_price():
    result = unit_prices(
        Decimal("5.99"), cat("per_volume"), {"volume_ml": 1892.705}, {}
    )
    assert result["per_volume"] == Decimal("0.003165")


def test_area_x_ply_needs_ply():
    c = cat("area_x_ply", ["per_roll"], "paper")
    measures = {"area_m2": 10.0, "rolls": 4.0}
    result = unit_prices(Decimal("8.00"), c, measures, {})
    assert "area_x_ply" not in result
    assert result["per_roll"] == Decimal("2.000000")
    result = unit_prices(Decimal("8.00"), c, measures, {"ply": AttrValue(2, "rule")})
    assert result["area_x_ply"] == Decimal("0.400000")


def test_per_100_sheets():
    c = cat("per_100_sheets", dimension="paper")
    result = unit_prices(Decimal("5.00"), c, {"sheets": 500.0}, {})
    assert result["per_100_sheets"] == Decimal("1.000000")


def test_zero_denominator_skipped():
    assert unit_prices(Decimal("5.99"), cat("per_volume"), {"volume_ml": 0}, {}) == {}


def test_per_load_price():
    c = cat("per_load", dimension="laundry")
    assert unit_prices(Decimal("12.97"), c, {"loads": 64}, {}) == {"per_load": Decimal("0.202656")}
    assert unit_prices(Decimal("12.97"), c, {"count": 81}, {}) == {}
