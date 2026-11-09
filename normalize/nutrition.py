"""Pure nutrition-per-dollar rules: never guess, never convert ml and g."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from normalize.types import Nutrition

REASONS = {
    "no_data": "no nutrition data from Open Food Facts",
    "per_serving": "nutrition per serving only",
    "basis_unknown": "nutrition basis unknown",
    "protein_missing": "protein not listed",
    "kcal_missing": "calories not listed",
    "implausible": "nutrition values look wrong",
    "pack_unknown": "pack size unknown",
    "per_100g_volume": "nutrition per 100 g, sold by volume",
    "per_100ml_mass": "nutrition per 100 ml, sold by weight",
    "zero_protein": "no protein",
    "zero_kcal": "no calories",
}

_Q = Decimal("0.000001")


def pack_of(
    measures: Mapping[str, float], measure_dim: str | None, measure_amount: float | None
) -> tuple[float | None, float | None]:
    """(mass_g, volume_ml) of the pack. Curated measures first, then the generic mass/volume."""
    mass = measures.get("mass_g")
    volume = measures.get("volume_ml")
    if mass is None and volume is None and measure_amount:
        if measure_dim == "mass":
            mass = measure_amount
        elif measure_dim == "volume":
            volume = measure_amount
    return (mass or None, volume or None)


def _result(n: Nutrition, pack, unit, protein_cost, kcal_cost, protein_reason, kcal_reason):
    return {
        "basis": n.basis,
        "protein": n.protein,
        "kcal": n.kcal,
        "pack": pack,
        "pack_unit": unit,
        "protein_cost": protein_cost,
        "kcal_cost": kcal_cost,
        "protein_reason": protein_reason,
        "kcal_reason": kcal_reason,
    }


def _plausible(n: Nutrition) -> bool:
    if n.protein is not None and not 0 <= n.protein <= 100:
        return False
    return n.kcal is None or 0 <= n.kcal <= 900


def offer_nutrition(
    price: Decimal,
    nutrition: Nutrition | None,
    measures: Mapping[str, float],
    measure_dim: str | None,
    measure_amount: float | None,
) -> dict:
    if nutrition is None:
        return {}
    n = nutrition
    mass, volume = pack_of(measures, measure_dim, measure_amount)

    def unranked(reason: str, pack=None, unit=None) -> dict:
        r = REASONS[reason]
        return _result(n, pack, unit, None, None, r, r)

    if n.basis == "serving":
        return unranked("per_serving")
    if n.basis not in ("100g", "100ml"):
        return unranked("basis_unknown")
    if not _plausible(n):
        return unranked("implausible")
    if mass is None and volume is None:
        return unranked("pack_unknown")
    if n.basis == "100g":
        if mass is None:
            return unranked("per_100g_volume")
        pack, unit = mass, "g"
    else:
        if volume is None:
            return unranked("per_100ml_mass")
        pack, unit = volume, "ml"

    def cost(value: float | None, scale: int, missing: str, zero: str):
        if value is None:
            return None, REASONS[missing]
        if value == 0:
            return None, REASONS[zero]
        c = price * scale / (Decimal(str(value)) * Decimal(str(pack)))
        return str(c.quantize(_Q)), None

    p_cost, p_reason = cost(n.protein, 10000, "protein_missing", "zero_protein")
    k_cost, k_reason = cost(n.kcal, 100000, "kcal_missing", "zero_kcal")
    return _result(n, pack, unit, p_cost, k_cost, p_reason, k_reason)
