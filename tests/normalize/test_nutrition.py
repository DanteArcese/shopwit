from decimal import Decimal

import pytest

from normalize.nutrition import REASONS, offer_nutrition, pack_of
from normalize.types import Nutrition

MASS = ({"mass_g": 907.0}, "mass", 907.0)
VOL = ({"volume_ml": 3785.0}, "volume", 3785.0)


def test_protein_and_kcal_costs_per_100g():
    n = offer_nutrition(Decimal("2.70"), Nutrition("100g", 21.0, 380.0), *MASS)
    assert n["protein_cost"] == "1.417546" and n["kcal_cost"] == "0.783381"
    assert (n["pack"], n["pack_unit"], n["protein_reason"]) == (907.0, "g", None)
    assert (n["basis"], n["protein"], n["kcal"], n["kcal_reason"]) == ("100g", 21.0, 380.0, None)


def test_per_100ml_with_volume_pack():
    n = offer_nutrition(Decimal("3.00"), Nutrition("100ml", 3.4, 50.0), *VOL)
    assert (n["protein_cost"], n["kcal_cost"], n["pack_unit"]) == ("2.331183", "1.585205", "ml")


def test_exact_keys():
    n = offer_nutrition(Decimal("2.70"), Nutrition("100g", 21.0, 380.0), *MASS)
    assert set(n) == {
        "basis", "protein", "kcal", "pack", "pack_unit",
        "protein_cost", "kcal_cost", "protein_reason", "kcal_reason",
    }  # fmt: skip


def test_never_fetched_is_empty():
    assert offer_nutrition(Decimal("1"), None, *MASS) == {}


def _both(n, reason):
    assert n["protein_cost"] is None and n["kcal_cost"] is None
    assert n["protein_reason"] == REASONS[reason] and n["kcal_reason"] == REASONS[reason]


def test_per_serving_only_is_unranked():
    _both(offer_nutrition(Decimal("2"), Nutrition("serving", 5.0, 100.0), *MASS), "per_serving")


def test_basis_unknown_is_unranked():
    _both(offer_nutrition(Decimal("2"), Nutrition(None, 5.0, 100.0), *MASS), "basis_unknown")


def test_per_100g_with_volume_pack_never_converts():
    _both(offer_nutrition(Decimal("2"), Nutrition("100g", 5.0, 100.0), *VOL), "per_100g_volume")


def test_per_100ml_with_mass_pack_never_converts():
    _both(offer_nutrition(Decimal("2"), Nutrition("100ml", 5.0, 100.0), *MASS), "per_100ml_mass")


def test_missing_protein_ranks_kcal_only():
    n = offer_nutrition(Decimal("2"), Nutrition("100g", None, 100.0), *MASS)
    assert n["protein_cost"] is None and n["protein_reason"] == REASONS["protein_missing"]
    assert n["kcal_cost"] is not None and n["kcal_reason"] is None


def test_missing_kcal_ranks_protein_only():
    n = offer_nutrition(Decimal("2"), Nutrition("100g", 5.0, None), *MASS)
    assert n["kcal_cost"] is None and n["kcal_reason"] == REASONS["kcal_missing"]
    assert n["protein_cost"] is not None and n["protein_reason"] is None


@pytest.mark.parametrize("protein,kcal", [(100.1, 380), (-1, 380), (21, 900.5), (21, -5)])
def test_implausible_values_unrank_both(protein, kcal):
    _both(offer_nutrition(Decimal("2"), Nutrition("100g", protein, kcal), *MASS), "implausible")


def test_protein_exactly_100_and_kcal_exactly_900_are_plausible():
    n = offer_nutrition(Decimal("2"), Nutrition("100g", 100.0, 900.0), *MASS)
    assert n["protein_cost"] is not None and n["kcal_cost"] is not None


def test_zero_protein_and_zero_kcal_are_unranked():
    n = offer_nutrition(Decimal("2"), Nutrition("100g", 0.0, 0.0), *MASS)
    assert n["protein_cost"] is None and n["protein_reason"] == REASONS["zero_protein"]
    assert n["kcal_cost"] is None and n["kcal_reason"] == REASONS["zero_kcal"]


@pytest.mark.parametrize(
    "pack",
    [({"count": 12.0}, "count", 12.0), ({}, "oz_labeled", 16.0), ({}, None, None)],
)
def test_unconvertible_pack_is_unranked(pack):
    n = offer_nutrition(Decimal("2"), Nutrition("100g", 5.0, 100.0), *pack)
    _both(n, "pack_unknown")
    assert n["pack"] is None and n["pack_unit"] is None


def test_curated_measures_win_over_generic():
    assert pack_of({"mass_g": 900.0}, "mass", 907.0) == (900.0, None)
    n = offer_nutrition(Decimal("2"), Nutrition("100g", 5.0, 100.0), {"mass_g": 900.0}, "mass", 907.0)
    assert n["pack"] == 900.0


def test_generic_measure_used_without_curated():
    assert pack_of({}, "mass", 907.0) == (907.0, None)
    assert pack_of({}, "volume", 500.0) == (None, 500.0)
    assert pack_of({"volume_ml": 250.0}, None, None) == (None, 250.0)


def test_checked_without_any_nutrition_is_no_data():
    # a 404 placeholder, an Open Products Facts entry or an OFF product without nutriments
    assert offer_nutrition(Decimal(2), Nutrition(None, None, None), *MASS) == {}


def test_serving_basis_without_values_is_still_per_serving():
    _both(offer_nutrition(Decimal(2), Nutrition("serving", None, None), *MASS), "per_serving")


@pytest.mark.parametrize("protein,kcal", [(1e-30, 380.0), (21.0, 1e-30)])
def test_tiny_values_are_unranked_not_raised(protein, kcal):
    n = offer_nutrition(Decimal(2), Nutrition("100g", protein, kcal), *MASS)
    _both(n, "implausible")


@pytest.mark.parametrize(
    "pack",
    [
        ({"mass_g": -907.0}, "mass", 907.0),
        ({"mass_g": float("nan")}, None, None),
        ({}, "mass", -907.0),
        ({}, "mass", float("nan")),
        ({}, "volume", 0.0),
    ],
)
def test_non_positive_or_nan_pack_is_pack_unknown(pack):
    n = offer_nutrition(Decimal(2), Nutrition("100g", 5.0, 100.0), *pack)
    _both(n, "pack_unknown")
    assert n["pack"] is None and n["pack_unit"] is None
