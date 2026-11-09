from decimal import Decimal

import pytest

from catalog.generic import GENERIC_TARGET_UNITS, generic_target_to_canonical
from catalog.units import (
    default_target,
    display_target,
    target_to_canonical,
    target_units_for,
)


def test_target_to_canonical():
    assert target_to_canonical(Decimal("128"), "floz", "volume") == ("volume_ml", Decimal("3785.412"))
    assert target_to_canonical(Decimal(1), "gal", "volume") == ("volume_ml", Decimal("3785.412"))
    assert target_to_canonical(Decimal("2"), "l", "volume") == ("volume_ml", Decimal("2000.000"))
    assert target_to_canonical(Decimal(12), "ct", "count") == ("count", Decimal("12.000"))
    assert target_to_canonical(Decimal(4), "rolls", "paper") == ("rolls", Decimal("4.000"))
    assert target_to_canonical(Decimal(1), "lb", "mass") == ("mass_g", Decimal("453.592"))


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1e9"])
def test_target_rejects(amount):
    with pytest.raises(ValueError):
        target_to_canonical(Decimal(amount), "floz", "volume")
    with pytest.raises(ValueError):
        generic_target_to_canonical(Decimal(amount), "floz", "volume")


def test_target_rejects_wrong_unit():
    with pytest.raises(ValueError):
        target_to_canonical(Decimal(1), "floz", "mass")
    with pytest.raises(ValueError):
        target_to_canonical(Decimal(1), "floz", "nope")


def test_default_target():
    assert default_target({"volume_ml": 1892.70592}, "volume", "imperial") == (Decimal("64.00"), "floz")
    assert default_target({"volume_ml": 1892.70592}, "volume", "metric") == (Decimal("1.89"), "l")
    assert default_target({}, "volume", "imperial") is None
    assert default_target({"sheets": 200}, "paper", "metric") == (Decimal("200.00"), "sheets")
    assert default_target({"rolls": 6, "sheets": 600}, "paper", "imperial") == (Decimal("6.00"), "rolls")


def test_target_units_for():
    assert target_units_for("volume", "imperial") == ["floz", "gal"]
    assert target_units_for("volume", "metric") == ["l", "ml"]
    assert target_units_for("mass", "imperial") == ["oz", "lb"]
    assert target_units_for("mass", "metric") == ["g", "kg"]
    for system in ("imperial", "metric"):
        assert target_units_for("count", system) == ["ct"]
        assert target_units_for("paper", system) == ["rolls", "sheets"]
        assert target_units_for("oz_labeled", system) == ["oz"]


def test_display_target():
    assert display_target("volume_ml", Decimal("3785.412"), "volume", "imperial") == "128 fl oz (1 gal)"
    assert display_target("count", Decimal("12.000"), "count", "metric") == "12 ct"


def test_generic_target_units():
    assert set(GENERIC_TARGET_UNITS) == {"volume", "mass", "count", "oz_labeled"}
    assert generic_target_to_canonical(Decimal(128), "floz", "volume") == Decimal("3785.412")
    assert generic_target_to_canonical(Decimal(2), "kg", "mass") == Decimal("2000.000")
    assert generic_target_to_canonical(Decimal(16), "oz", "oz_labeled") == Decimal("16.000")
    assert GENERIC_TARGET_UNITS["oz_labeled"]["oz"] == ("oz (as labeled)", Decimal(1))
    with pytest.raises(ValueError):
        generic_target_to_canonical(Decimal(1), "floz", "oz_labeled")
    with pytest.raises(ValueError):
        generic_target_to_canonical(Decimal(1), "ct", "paper")


def test_laundry_and_tissue_targets():
    for system in ("imperial", "metric"):
        assert target_units_for("laundry", system) == ["loads"]
        assert target_units_for("tissue", system) == ["sheets"]
    assert target_to_canonical(Decimal(64), "loads", "laundry") == ("loads", Decimal("64.000"))
    assert target_to_canonical(Decimal(500), "sheets", "tissue") == ("sheets", Decimal("500.000"))
    with pytest.raises(ValueError):
        target_to_canonical(Decimal(1), "rolls", "tissue")
    assert default_target({"loads": 64, "volume_ml": 2720.76}, "laundry", "metric") == (Decimal("64.00"), "loads")
    assert default_target({"volume_ml": 2720.76}, "laundry", "metric") is None
    assert default_target({"boxes": 8, "sheets": 960}, "tissue", "imperial") == (Decimal("960.00"), "sheets")
    assert default_target({}, "tissue", "metric") is None


def test_display_target_laundry_tissue():
    assert display_target("loads", Decimal("64.000"), "laundry", "metric") == "64 loads"
    assert display_target("loads", Decimal("1.000"), "laundry", "imperial") == "1 load"
    assert display_target("sheets", Decimal("960.000"), "tissue", "imperial") == "960 sheets"
    assert display_target("sheets", Decimal("200.000"), "paper", "imperial") == "200 sheets"


def test_sheets_dimension_resolved_by_category():
    from lists.present import dimension_of

    assert dimension_of("sheets") == "paper"
    assert dimension_of("sheets", "tissue") == "tissue"
    assert dimension_of("sheets", "paper") == "paper"
    assert dimension_of("loads") == "laundry"
