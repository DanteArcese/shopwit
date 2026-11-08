"""Filtering and unit-price ranking for category browsing.

Honest ranking: order depends only on unit price (ties: price, title, retailer).
With an attribute filter active, products whose value is unknown are excluded;
otherwise they are included and the unknown is labelled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from normalize.compare import COMPARISONS

from .freshness import fresh_observed, fresh_offers
from .models import Category, Offer, Product

BRAND_TIERS = ("name_brand", "store_brand")
SIZE_KEYS = {"volume": "volume_ml", "mass": "mass_g", "count": "count", "paper": "area_m2"}
# Relative slack so float conversion noise never drops an exact-size match.
SIZE_TOLERANCE = 1e-9
# Attributes a comparison's unit price depends on, beyond quantity.
COMPARISON_ATTRS = {"area_x_ply": {"ply"}}


@dataclass(frozen=True)
class FilterSpec:
    attributes: Mapping[str, object]
    size_min: float | None = None
    size_max: float | None = None
    brand_tier: str | None = None


@dataclass(frozen=True)
class Row:
    product: Product
    offer: Offer
    unit_price: Decimal | None
    unknown: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True)
class BrowseResult:
    ranked: list[Row]
    unranked: list[Row]
    comparison: str
    total_products: int
    unranked_total: int = 0


def _parse_value(attr, raw: str):
    values = attr.values or []
    if attr.type == "enum":
        return raw if raw in values else None
    if attr.type == "bool":
        return {"true": True, "false": False}.get(raw)
    if attr.type == "int":
        if not (raw.isascii() and raw.isdigit()):
            return None
        n = int(raw)
        return n if not values or n in values else None
    return None


def _parse_number(raw: str | None) -> float | None:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def parse_filters(category: Category, params: Mapping[str, str]) -> FilterSpec:
    attributes: dict[str, object] = {}
    for attr in category.attribute_defs.all():
        raw = params.get(attr.name)
        if raw is None or raw == "any":
            continue
        value = _parse_value(attr, raw)
        if value is not None:
            attributes[attr.name] = value
    tier = params.get("brand_tier")
    return FilterSpec(
        attributes=attributes,
        size_min=_parse_number(params.get("size_min")),
        size_max=_parse_number(params.get("size_max")),
        brand_tier=tier if tier in BRAND_TIERS else None,
    )


def _unit_price(offer: Offer, comparison: str) -> Decimal | None:
    raw = offer.unit_prices.get(comparison)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _reason(offer: Offer, comparison: str) -> str:
    unit = COMPARISONS[comparison].unit_label if comparison in COMPARISONS else "unit"
    related = COMPARISON_ATTRS.get(comparison, set())
    if any(e == "quantity" or e.startswith("quantity:") for e in offer.unresolved):
        return "size unknown"
    for entry in offer.unresolved:
        if entry.startswith("attribute:"):
            name = entry.split(":")[1]
            if name in related:
                return f"{name.replace('_', ' ')} unknown"
        elif entry == f"unit_price:{comparison}":
            return f"can't compute price per {unit}"
    return f"can't compute price per {unit}"


def browse(
    category: Category,
    spec: FilterSpec,
    comparison: str | None,
    stores: frozenset[str] | None,
    now: datetime,
    limit: int = 50,
    *,
    observed: bool = False,
) -> BrowseResult:
    allowed = [category.comparison_default, *category.comparison_alternates]
    if comparison not in allowed:
        comparison = category.comparison_default

    qs = (fresh_observed(now) if observed else fresh_offers(now)).filter(product__category=category)
    if stores is not None:
        qs = qs.filter(retailer__in=stores)
    for name, value in spec.attributes.items():
        qs = qs.filter(product__attributes__contains={name: {"value": value}})
    if spec.brand_tier:
        qs = qs.filter(product__attributes__contains={"brand_tier": {"value": spec.brand_tier}})

    key = SIZE_KEYS.get(category.dimension)
    offers = list(qs.select_related("product", "store") if observed else qs.select_related("product"))
    if spec.size_min is not None or spec.size_max is not None:
        sized = []
        for o in offers:
            size = o.measures.get(key)
            if size is None:
                continue
            if spec.size_min is not None and size < spec.size_min * (1 - SIZE_TOLERANCE):
                continue
            if spec.size_max is not None and size > spec.size_max * (1 + SIZE_TOLERANCE):
                continue
            sized.append(o)
        offers = sized

    by_product: dict[int, list[Offer]] = {}
    for o in offers:
        by_product.setdefault(o.product_id, []).append(o)

    attr_names = [a.name for a in category.attribute_defs.all()]
    ranked: list[Row] = []
    unranked: list[Row] = []
    for group in by_product.values():
        product = group[0].product
        unknown = tuple(
            n for n in attr_names if n not in spec.attributes and n not in product.attributes
        )
        priced = [(up, o) for o in group if (up := _unit_price(o, comparison)) is not None]
        if priced:
            up, offer = min(priced, key=lambda t: (t[0], t[1].price, t[1].retailer))
            ranked.append(Row(product, offer, up, unknown, None))
        else:
            offer = min(group, key=lambda o: (o.price, o.retailer))
            unranked.append(Row(product, offer, None, unknown, _reason(offer, comparison)))

    ranked.sort(key=lambda r: (r.unit_price, r.offer.price, r.product.title, r.offer.retailer))
    unranked.sort(key=lambda r: (r.offer.price, r.product.title, r.offer.retailer))
    return BrowseResult(
        ranked=ranked[:limit],
        unranked=unranked[:limit],
        unranked_total=len(unranked),
        comparison=comparison,
        total_products=len(ranked) + len(unranked),
    )
