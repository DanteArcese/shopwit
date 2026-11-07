"""The normalize() pipeline: raw offer to normalized offer."""

from __future__ import annotations

from collections.abc import Mapping

from normalize.categories import CategoryDef
from normalize.compare import unit_prices
from normalize.extract import extract_attributes
from normalize.match import brand_tier, match_category
from normalize.quantity import parse_measures
from normalize.types import NormalizedOffer, OffProduct, RawOffer


def normalize(
    raw: RawOffer,
    categories: Mapping[str, CategoryDef],
    store_brands: frozenset[str],
    off: OffProduct | None = None,
) -> NormalizedOffer:
    slug = match_category(raw, categories)
    if slug is None:
        return NormalizedOffer(raw, None, {}, {}, {}, ("category",))
    category = categories[slug]

    attributes, unresolved = extract_attributes(raw, category, off)

    tier = brand_tier(raw, store_brands)
    if tier is None:
        unresolved.append("attribute:brand_tier")
    else:
        attributes["brand_tier"] = tier

    measures = parse_measures(
        [raw.size_text or "", raw.title, (off.quantity if off else None) or ""],
        category.dimension,
    )
    if not measures:
        unresolved.append("quantity")

    prices = unit_prices(raw.price, category, measures, attributes)
    if category.comparison_default not in prices:
        unresolved.append(f"unit_price:{category.comparison_default}")

    return NormalizedOffer(raw, slug, attributes, measures, prices, tuple(unresolved))
