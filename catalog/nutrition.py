"""Nutrition per dollar on offers: rank parsing, stored costs and the refresh helper."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from normalize.nutrition import REASONS, offer_nutrition
from normalize.types import Nutrition

from .models import OffEntry, Offer

RANKS = {"unit": "Price per unit", "protein": "Protein per $", "kcal": "Calories per $"}

_FIELDS = {"protein": ("protein_cost", "protein_reason"), "kcal": ("kcal_cost", "kcal_reason")}


def parse_rank(raw: str | None) -> str:
    """Exact match only; anything else ranks by price per unit."""
    return raw if raw in ("protein", "kcal") else "unit"


def nutrition_cost(offer: Offer, rank: str) -> Decimal | None:
    if rank not in _FIELDS:
        return None
    value = (offer.nutrition or {}).get(_FIELDS[rank][0])
    return Decimal(value) if value is not None else None


def nutrition_reason(offer: Offer, rank: str) -> str:
    if not offer.nutrition:
        return REASONS["no_data"]
    if rank not in _FIELDS:
        return ""
    return offer.nutrition.get(_FIELDS[rank][1]) or ""


def refresh_nutrition(upcs: Iterable[str], chunk: int = 1000) -> int:
    """Recompute ``Offer.nutrition`` for every offer on these UPCs from the current OffEntry.

    Writes only changed rows; returns how many changed.
    """
    codes = sorted(set(upcs))
    changed = 0
    for i in range(0, len(codes), chunk):
        part = codes[i : i + chunk]
        entries = {
            e.upc: Nutrition(e.nutrition_per, e.protein_100g, e.kcal_100g)
            for e in OffEntry.objects.filter(upc__in=part, nutrition_checked_at__isnull=False)
        }
        offers = list(Offer.objects.filter(product__upc__in=part).select_related("product"))
        dirty = []
        for offer in offers:
            new = offer_nutrition(
                offer.price,
                entries.get(offer.product.upc),
                offer.measures or {},
                offer.measure_dim,
                offer.measure_amount,
            )
            if new != offer.nutrition:
                offer.nutrition = new
                dirty.append(offer)
        if dirty:
            Offer.objects.bulk_update(dirty, ["nutrition"])
            changed += len(dirty)
    return changed
