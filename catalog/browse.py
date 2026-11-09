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

from django.db.models import Q

from normalize.compare import COMPARISONS

from .freshness import fresh_observed, fresh_offers
from .geo import Near, bounding_box, within
from .models import Category, Offer, Product

BRAND_TIERS = ("name_brand", "store_brand")
SIZE_KEYS = {"volume": "volume_ml", "mass": "mass_g", "count": "count", "paper": "area_m2", "laundry": "loads"}
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
    hidden_far: int = 0  # products hidden because their stores are outside the radius
    hidden_unplaced: int = 0  # products hidden only because their stores have no location


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


def spec_params(spec: FilterSpec) -> dict[str, str]:
    """Canonical query params for a spec: the inverse of ``parse_filters``."""
    params: dict[str, str] = {}
    for name, value in spec.attributes.items():
        params[name] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    if spec.size_min is not None:
        params["size_min"] = repr(spec.size_min)
    if spec.size_max is not None:
        params["size_max"] = repr(spec.size_max)
    if spec.brand_tier:
        params["brand_tier"] = spec.brand_tier
    return params


def parse_filters_strict(
    category: Category, params: Mapping[str, str]
) -> tuple[FilterSpec, tuple[str, ...]]:
    """Like ``parse_filters``, plus the sorted keys that were set (not "any") but did not parse."""
    spec = parse_filters(category, params)
    defs = {a.name: a for a in category.attribute_defs.all()}
    invalid: list[str] = []
    for key, raw in params.items():
        if raw == "any":
            continue
        if key in defs:
            ok = key in spec.attributes
        elif key == "brand_tier":
            ok = spec.brand_tier is not None
        elif key == "size_min":
            ok = spec.size_min is not None
        elif key == "size_max":
            ok = spec.size_max is not None
        else:
            ok = False
        if not ok:
            invalid.append(key)
    return spec, tuple(sorted(invalid))


def _unit_price(offer: Offer, comparison: str) -> Decimal | None:
    raw = offer.unit_prices.get(comparison)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _tie(offer: Offer) -> tuple:
    """Final tie-break: for observed offers newest observation first, then store code."""
    if offer.kind != Offer.Kind.OBSERVED:
        return ()
    day = offer.observed_on.toordinal() if offer.observed_on else 0
    return (-day, offer.store.code if offer.store else "")


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


def _size_ok(measures: Mapping, key: str | None, spec: FilterSpec) -> bool:
    if spec.size_min is None and spec.size_max is None:
        return True
    size = measures.get(key)
    if size is None:
        return False
    if spec.size_min is not None and size < spec.size_min * (1 - SIZE_TOLERANCE):
        return False
    if spec.size_max is not None and size > spec.size_max * (1 + SIZE_TOLERANCE):
        return False
    return True


def _in_box(near: Near) -> Q:
    """Stores inside the radius's bounding box (coordinate-less stores never match)."""
    min_lat, max_lat, min_lon, max_lon = bounding_box(near)
    q = Q(store__lat__gte=min_lat, store__lat__lte=max_lat, store__lon__isnull=False)
    if min_lon is not None:
        q &= Q(store__lon__gte=min_lon, store__lon__lte=max_lon)
    return q


def _exact_radius(offers: list[Offer], near: Near) -> tuple[list[Offer], set[int]]:
    """Offers at stores within the radius, and the products of those beyond it."""
    kept: list[Offer] = []
    far: set[int] = set()
    for o in offers:
        if o.store is not None and within(near, o.store.lat, o.store.lon):
            kept.append(o)
        else:
            far.add(o.product_id)
    return kept, far


def limit_to_radius(offers: list[Offer], near: Near | None) -> tuple[list[Offer], int, int]:
    """Keep offers at stores within the radius.

    Returns (kept offers, hidden_far, hidden_unplaced) where the counts are products
    left with no offer: hidden_far if any of its offers is at a placed store beyond
    the radius, else hidden_unplaced (stores without coordinates, or no store).
    Offers must have ``store`` loaded. With ``near`` None nothing is filtered.
    """
    if near is None:
        return offers, 0, 0
    kept: list[Offer] = []
    far: set[int] = set()
    unplaced: set[int] = set()
    for o in offers:
        store = o.store
        if store is None or store.lat is None or store.lon is None:
            unplaced.add(o.product_id)
        elif within(near, store.lat, store.lon):
            kept.append(o)
        else:
            far.add(o.product_id)
    kept_products = {o.product_id for o in kept}
    return kept, len(far - kept_products), len(unplaced - kept_products - far)


def _filtered_qs(category: Category, spec: FilterSpec, stores: frozenset[str] | None, qs):
    # Offers with no category (generic) never appear on a curated page, even when
    # they share a product with a curated offer.
    qs = qs.filter(product__category=category, category=category)
    if stores is not None:
        qs = qs.filter(retailer__in=stores)
    for name, value in spec.attributes.items():
        qs = qs.filter(product__attributes__contains={name: {"value": value}})
    if spec.brand_tier:
        qs = qs.filter(product__attributes__contains={"brand_tier": {"value": spec.brand_tier}})
    return qs


def matching_offers(
    category: Category, spec: FilterSpec, stores: frozenset[str] | None, now: datetime
) -> list[Offer]:
    """Fresh live offers passing the spec (attributes, brand tier, size range) at the stores."""
    qs = _filtered_qs(category, spec, stores, fresh_offers(now)).defer("search_vector")
    key = SIZE_KEYS.get(category.dimension)
    return [o for o in qs.select_related("product") if _size_ok(o.measures, key, spec)]


def browse(
    category: Category,
    spec: FilterSpec,
    comparison: str | None,
    stores: frozenset[str] | None,
    now: datetime,
    limit: int = 50,
    *,
    observed: bool = False,
    near: Near | None = None,
) -> BrowseResult:
    """Rank offers by unit price.

    With ``near`` (observed offers only), offers from stores outside the radius
    or without coordinates are left out. A product left with no offer counts in
    ``hidden_far`` if any of its offers is at a placed store beyond the radius,
    else in ``hidden_unplaced``. A bounding box narrows rows in SQL; the exact
    haversine check runs on what is left. Costs one extra query.
    """
    allowed = [category.comparison_default, *category.comparison_alternates]
    if comparison not in allowed:
        comparison = category.comparison_default

    qs = _filtered_qs(
        category, spec, stores, fresh_observed(now) if observed else fresh_offers(now)
    )

    key = SIZE_KEYS.get(category.dimension)
    near = near if observed else None  # live offers are never filtered by distance
    far_products: set[int] = set()
    unplaced_products: set[int] = set()
    if near is not None:
        box = _in_box(near)
        outside = qs.exclude(box).values_list("product_id", "measures", "store__lat", "store__lon")
        for product_id, measures, lat, lon in outside:
            if _size_ok(measures, key, spec):
                placed = lat is not None and lon is not None
                (far_products if placed else unplaced_products).add(product_id)
        qs = qs.filter(box)
    qs = qs.defer("search_vector")  # the text-search column is never read here
    offers = list(qs.select_related("product", "store") if observed else qs.select_related("product"))
    offers = [o for o in offers if _size_ok(o.measures, key, spec)]
    if near is not None:
        offers, far_inside = _exact_radius(offers, near)
        far_products |= far_inside

    by_product: dict[int, list[Offer]] = {}
    for o in offers:
        by_product.setdefault(o.product_id, []).append(o)
    hidden_far = len(far_products - by_product.keys())
    hidden_unplaced = len(unplaced_products - by_product.keys() - far_products)

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
            up, offer = min(priced, key=lambda t: (t[0], t[1].price, t[1].retailer, _tie(t[1])))
            ranked.append(Row(product, offer, up, unknown, None))
        else:
            offer = min(group, key=lambda o: (o.price, o.retailer, _tie(o)))
            unranked.append(Row(product, offer, None, unknown, _reason(offer, comparison)))

    ranked.sort(key=lambda r: (r.unit_price, r.offer.price, r.product.title, r.offer.retailer, _tie(r.offer)))
    unranked.sort(key=lambda r: (r.offer.price, r.product.title, r.offer.retailer, _tie(r.offer)))
    return BrowseResult(
        ranked=ranked[:limit],
        unranked=unranked[:limit],
        unranked_total=len(unranked),
        comparison=comparison,
        total_products=len(ranked) + len(unranked),
        hidden_far=hidden_far,
        hidden_unplaced=hidden_unplaced,
    )
