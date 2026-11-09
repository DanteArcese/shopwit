"""Comparison methods and unit-price computation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from normalize.categories import CategoryDef
from normalize.types import AttrValue

Measures = Mapping[str, float]
Denominator = Callable[[Measures, Mapping[str, AttrValue]], "float | None"]


@dataclass(frozen=True)
class Comparison:
    name: str
    unit_label: str
    denominator: Denominator


def _measure(key: str, scale: float = 1.0) -> Denominator:
    def denominator(measures: Measures, attributes: Mapping[str, AttrValue]) -> float | None:
        value = measures.get(key)
        return None if value is None else value / scale

    return denominator


def _area_x_ply(measures: Measures, attributes: Mapping[str, AttrValue]) -> float | None:
    area = measures.get("area_m2")
    ply = attributes.get("ply")
    if area is None or ply is None:
        return None
    return area * int(ply.value)


def _sheets_x_ply(measures: Measures, attributes: Mapping[str, AttrValue]) -> float | None:
    sheets = measures.get("sheets")
    ply = attributes.get("ply")
    if sheets is None or ply is None:
        return None
    return sheets * int(ply.value) / 100.0


COMPARISONS: dict[str, Comparison] = {
    c.name: c
    for c in (
        Comparison("per_volume", "mL", _measure("volume_ml")),
        Comparison("per_mass", "g", _measure("mass_g")),
        Comparison("per_count", "each", _measure("count")),
        Comparison("area_x_ply", "m²·ply", _area_x_ply),
        Comparison("per_100_sheets", "100 sheets", _measure("sheets", 100.0)),
        Comparison("per_roll", "roll", _measure("rolls")),
        Comparison("per_load", "load", _measure("loads")),
        Comparison("sheets_x_ply", "100 sheets·ply", _sheets_x_ply),
    )
}


def unit_prices(
    price: Decimal,
    category: CategoryDef,
    measures: Measures,
    attributes: Mapping[str, AttrValue],
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for name in (category.comparison_default, *category.comparison_alternates):
        d = COMPARISONS[name].denominator(measures, attributes)
        if d is None or d <= 0:
            continue
        result[name] = (price / Decimal(str(d))).quantize(Decimal("0.000001"))
    return result
