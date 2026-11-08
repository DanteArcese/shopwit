"""Generic (category-free) search: text matching, trigram fallback and measure grouping.

Text relevance only picks the population (at most MATCH_CAP products); it never orders rows.
Rows order by generic unit price. Nothing here compares numbers across dimensions.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml
from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    TrigramWordSimilarity,
)
from django.db.models import Max
from django.http import QueryDict

from catalog.browse import BRAND_TIERS, SIZE_TOLERANCE, _tie
from catalog.freshness import fresh_observed, fresh_offers
from catalog.models import Offer, Product
from catalog.units import G_PER_OZ, ML_PER_FL_OZ, size_to_canonical
from normalize.categories import CategoryDef
from normalize.generic import DIMENSION_ORDER, trigram_similarity
from normalize.match import brand_tier_from_field
from normalize.text import fold

MATCH_CAP = 500
FUZZY_BELOW = 5
GROUP_PAGE = 25
SIZE_UNKNOWN = "size unknown"
SIZES_DISAGREE = "sizes disagree between stores"


@dataclass(frozen=True)
class Match:
    offers: list[Offer]
    fuzzy: bool
    capped: bool


@dataclass(frozen=True)
class GenericRow:
    product: Product
    offer: Offer
    unit_price: Decimal | None
    reason: str | None


@dataclass(frozen=True)
class MeasureGroup:
    dimension: str
    rows: list[GenericRow]
    total: int


@dataclass(frozen=True)
class GenericResult:
    groups: list[MeasureGroup]
    unknown: list[GenericRow]
    total_products: int


_EMPTY = Match(offers=[], fuzzy=False, capped=False)


def match_offers(
    q: str, now: datetime, *, observed: bool = False, stores: frozenset[str] | None = None
) -> Match:
    """Held offers whose product matches the folded query q. At most 3 queries."""
    if not q or not q.strip():
        return _EMPTY
    population = fresh_observed(now) if observed else fresh_offers(now)
    if stores is not None:
        population = population.filter(retailer__in=stores)

    query = SearchQuery(q, config="english", search_type="websearch")
    ranked = (
        population.filter(search_vector=query)
        .values("product_id")
        .annotate(rank=Max(SearchRank("search_vector", query)))
        .order_by("-rank", "product_id")
    )
    ids = [row["product_id"] for row in ranked[: MATCH_CAP + 1]]
    capped = len(ids) > MATCH_CAP
    ids = ids[:MATCH_CAP]

    fuzzy = False
    if len(ids) < FUZZY_BELOW:
        similar = (
            population.filter(title__trigram_word_similar=q)
            .exclude(product_id__in=ids)
            .values("product_id")
            .annotate(sim=Max(TrigramWordSimilarity(q, "title")))
            .order_by("-sim", "product_id")
        )
        extra = [row["product_id"] for row in similar[: MATCH_CAP - len(ids)]]
        if extra:
            ids += extra
            fuzzy = True
    if not ids:
        return _EMPTY

    offers = list(
        population.filter(product_id__in=ids)
        .select_related("product", "product__category", "store")
        .order_by("id")
    )
    return Match(offers=offers, fuzzy=fuzzy, capped=capped)


def _unit_key(offer: Offer) -> tuple:
    unit = offer.generic_unit_price
    return (unit is None, unit if unit is not None else Decimal(0), offer.price, offer.retailer, _tie(offer), offer.id)


def _price_key(offer: Offer) -> tuple:
    return (offer.price, offer.retailer, _tie(offer), offer.id)


def group_products(offers: Iterable[Offer]) -> GenericResult:
    by_product: dict[int, list[Offer]] = {}
    for offer in offers:
        by_product.setdefault(offer.product_id, []).append(offer)

    grouped: dict[str, list[GenericRow]] = {}
    unknown: list[GenericRow] = []
    for product_offers in by_product.values():
        product = product_offers[0].product
        dims = {o.measure_dim for o in product_offers if o.measure_dim}
        if len(dims) == 1:
            dim = next(iter(dims))
            best = min((o for o in product_offers if o.measure_dim == dim), key=_unit_key)
            grouped.setdefault(dim, []).append(GenericRow(product, best, best.generic_unit_price, None))
        else:
            cheapest = min(product_offers, key=_price_key)
            reason = SIZES_DISAGREE if dims else (cheapest.measure_reason or SIZE_UNKNOWN)
            unknown.append(GenericRow(product, cheapest, None, reason))

    def row_key(row: GenericRow) -> tuple:
        o = row.offer
        unit = row.unit_price
        return (unit is None, unit if unit is not None else Decimal(0), o.price, row.product.title, o.retailer, o.id)

    order = {d: i for i, d in enumerate(DIMENSION_ORDER)}
    groups = [
        MeasureGroup(dimension=dim, rows=sorted(rows, key=row_key), total=len(rows))
        for dim, rows in grouped.items()
    ]
    groups.sort(key=lambda g: (-g.total, order.get(g.dimension, len(order))))
    unknown.sort(key=lambda r: (r.offer.price, r.product.title, r.offer.retailer, r.offer.id))
    return GenericResult(groups=groups, unknown=unknown, total_products=len(by_product))


NEAR_MISS_MIN_HELD = 2
NEAR_MISS_SIMILARITY = 0.4
NEAR_MISS_LIMIT = 3


@dataclass(frozen=True)
class NearMiss:
    slug: str
    name: str


def near_misses(
    folded_q: str,
    offers: Iterable[Offer],
    definitions: Mapping[str, CategoryDef],
    names: Mapping[str, str],
) -> list[NearMiss]:
    """Categories worth offering instead of the generic list.

    Held-result candidates (active categories with at least two matched products) come
    first, by product count. Typo candidates follow, by similarity. Capped at 3.
    """
    products_by_slug: dict[str, set[int]] = {}
    for offer in offers:
        category = offer.product.category
        if category is not None and category.slug in definitions:
            products_by_slug.setdefault(category.slug, set()).add(offer.product_id)
    held = sorted(
        ((len(ids), slug) for slug, ids in products_by_slug.items() if len(ids) >= NEAR_MISS_MIN_HELD),
        key=lambda t: (-t[0], t[1]),
    )
    typos: list[tuple[float, str]] = []
    if folded_q:
        for slug, definition in definitions.items():
            best = max(
                (trigram_similarity(folded_q, text) for text in (definition.name, *definition.include)),
                default=0.0,
            )
            if best >= NEAR_MISS_SIMILARITY:
                typos.append((best, slug))
    typos.sort(key=lambda t: (-t[0], t[1]))
    out: list[NearMiss] = []
    seen: set[str] = set()
    for slug in [s for _, s in held] + [s for _, s in typos]:
        if slug not in seen:
            seen.add(slug)
            out.append(NearMiss(slug, names.get(slug, definitions[slug].name)))
    return out[:NEAR_MISS_LIMIT]


# ---- data-derived facets ----------------------------------------------------------------

SIZE_PARAMS = {"volume": "vol", "mass": "mass", "oz_labeled": "oz", "count": "count"}


@dataclass(frozen=True)
class GenericFilters:
    brands: frozenset[str]
    brand_tier: str | None
    labels: frozenset[str]
    path: str | None
    sizes: Mapping[str, tuple[float | None, float | None]]

    @property
    def active(self) -> bool:
        return bool(self.brands or self.brand_tier or self.labels or self.path or self.sizes)


@dataclass(frozen=True)
class Facet:
    name: str
    options: list[tuple[str, str, int]]
    active: bool


def load_label_allowlist(path: Path) -> dict[str, str]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {str(tag): str(name) for tag, name in data.items()}


def _bound(raw: str | None, dimension: str, unit_system: str) -> float | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
        if not value.is_finite() or value < 0:
            return None
        result = size_to_canonical(value, dimension, unit_system)
    except (ArithmeticError, OverflowError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_generic_filters(
    params: QueryDict, unit_system: str, allowlist: Mapping[str, str]
) -> GenericFilters:
    """Facet filters from the URL. Invalid or unknown values are dropped, never an error."""
    brands = frozenset(b for b in (fold(v) for v in params.getlist("brand")) if b)
    tier = params.get("brand_tier")
    labels = frozenset(v for v in params.getlist("label") if v in allowlist)
    path = (params.get("path") or "").strip().strip("/") or None
    sizes: dict[str, tuple[float | None, float | None]] = {}
    for dimension, key in SIZE_PARAMS.items():
        lo = _bound(params.get(f"{key}_min"), dimension, unit_system)
        hi = _bound(params.get(f"{key}_max"), dimension, unit_system)
        if lo is not None or hi is not None:
            sizes[dimension] = (lo, hi)
    return GenericFilters(brands, tier if tier in BRAND_TIERS else None, labels, path, sizes)


def _tier(offer: Offer, store_brands: frozenset[str]) -> str | None:
    value = brand_tier_from_field(offer.brand, store_brands)
    return str(value.value) if value else None


def _in_path(offer: Offer, path: str) -> bool:
    cp = offer.category_path
    return bool(cp) and (cp == path or cp.startswith(path + "/"))


def _size_ok(offer: Offer, sizes: Mapping[str, tuple[float | None, float | None]]) -> bool:
    bounds = sizes.get(offer.measure_dim or "")
    if bounds is None or offer.measure_amount is None:
        return False
    lo, hi = bounds
    if lo is not None and offer.measure_amount < lo * (1 - SIZE_TOLERANCE):
        return False
    return not (hi is not None and offer.measure_amount > hi * (1 + SIZE_TOLERANCE))


def _passes(offer: Offer, f: GenericFilters, store_brands: frozenset[str], skip: str = "") -> bool:
    if f.brands and skip != "brand" and fold(offer.brand or "") not in f.brands:
        return False
    if f.brand_tier and skip != "brand_tier" and _tier(offer, store_brands) != f.brand_tier:
        return False
    if f.labels and skip != "labels" and not f.labels <= set(offer.labels or ()):
        return False
    if f.path and skip != "path" and not _in_path(offer, f.path):
        return False
    return not (f.sizes and skip != "size" and not _size_ok(offer, f.sizes))


def apply_filters(
    offers: Iterable[Offer], f: GenericFilters, store_brands: frozenset[str]
) -> list[Offer]:
    """Offers passing every active filter. An unknown value under an active filter is excluded.

    Size bounds on several dimensions are alternatives: an offer is kept when the bounds
    for its own dimension hold. Offers in a dimension with no bound are dropped.
    """
    return [o for o in offers if _passes(o, f, store_brands)]


def _counts(pairs: Iterable[tuple[str, int]]) -> dict[str, int]:
    """Distinct product count per value."""
    seen: dict[str, set[int]] = {}
    for value, product_id in pairs:
        seen.setdefault(value, set()).add(product_id)
    return {v: len(ids) for v, ids in seen.items()}


def _display_amount(dimension: str, amount: float, unit_system: str) -> float:
    imperial = unit_system == "imperial"
    if dimension == "volume":
        return amount / float(ML_PER_FL_OZ) if imperial else amount / 1000
    if dimension == "mass":
        return amount / float(G_PER_OZ) if imperial else amount / 1000
    return amount


def _fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def facets(
    offers: list[Offer],
    f: GenericFilters,
    store_brands: frozenset[str],
    allowlist: Mapping[str, str],
    unit_system: str,
) -> list[Facet]:
    """Facets over the offers; each sees the offers passing every other active filter."""
    out: list[Facet] = []

    pools: dict[str, list[Offer]] = {}

    def pool(skip: str) -> list[Offer]:
        if skip not in pools:
            pools[skip] = [o for o in offers if _passes(o, f, store_brands, skip)]
        return pools[skip]

    # brand
    spellings: dict[str, Counter] = {}
    for o in pool("brand"):
        key = fold(o.brand or "")
        if key:
            spellings.setdefault(key, Counter())[o.brand] += 1
    counts = _counts(
        (fold(o.brand or ""), o.product_id) for o in pool("brand") if fold(o.brand or "")
    )
    if len(counts) >= 2 or f.brands:
        options = []
        for key in sorted(set(counts) | f.brands):
            names = spellings.get(key)
            label = min(names, key=lambda n: (-names[n], n)) if names else key
            options.append((key, label, counts.get(key, 0)))
        options.sort(key=lambda t: (-t[2], t[1].lower(), t[1]))
        out.append(Facet("brand", options, bool(f.brands)))

    # brand tier
    counts = _counts(
        (t, o.product_id) for o in pool("brand_tier") if (t := _tier(o, store_brands))
    )
    if len(counts) >= 2 or f.brand_tier:
        names = {"name_brand": "Name brand", "store_brand": "Store brand"}
        out.append(
            Facet(
                "brand_tier",
                [(t, names[t], counts.get(t, 0)) for t in BRAND_TIERS if t in counts or t == f.brand_tier],
                bool(f.brand_tier),
            )
        )

    # labels
    base = pool("labels")
    all_products = {o.product_id for o in base}
    options = []
    for tag, name in allowlist.items():
        have = {o.product_id for o in base if tag in (o.labels or ())}
        if (have and have != all_products) or tag in f.labels:
            options.append((tag, f"{name} (from Open Food Facts)", len(have)))
    if options:
        out.append(Facet("labels", options, bool(f.labels)))

    # path
    prefix = f.path
    nxt: list[tuple[str, int]] = []
    for o in pool("path"):
        cp = o.category_path
        if not cp:
            continue
        if prefix is None:
            nxt.append((cp.split("/")[0], o.product_id))
        elif cp.startswith(prefix + "/"):
            nxt.append((prefix + "/" + cp[len(prefix) + 1 :].split("/")[0], o.product_id))
    counts = _counts(nxt)
    if len(counts) >= 2 or f.path:
        out.append(
            Facet(
                "path",
                [(v, v.rsplit("/", 1)[-1], n) for v, n in sorted(counts.items())],
                bool(f.path),
            )
        )

    # size, one facet per dimension
    base = pool("size")
    for dimension, key in SIZE_PARAMS.items():
        amounts = {
            o.measure_amount
            for o in base
            if o.measure_dim == dimension and o.measure_amount is not None
        }
        if len(amounts) >= 2 or dimension in f.sizes:
            if not amounts:
                continue
            lo = math.floor(_display_amount(dimension, min(amounts), unit_system) * 100) / 100
            hi = math.ceil(_display_amount(dimension, max(amounts), unit_system) * 100) / 100
            products = len({o.product_id for o in base if o.measure_dim == dimension})
            out.append(
                Facet(key, [("min", _fmt(lo), products), ("max", _fmt(hi), products)], dimension in f.sizes)
            )
    return out
