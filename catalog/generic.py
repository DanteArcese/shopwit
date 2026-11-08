"""Generic (category-free) search: text matching, trigram fallback and measure grouping.

Text relevance only picks the population (at most MATCH_CAP products); it never orders rows.
Rows order by generic unit price. Nothing here compares numbers across dimensions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramWordSimilarity
from django.db.models import Max

from catalog.browse import _tie
from catalog.freshness import fresh_observed, fresh_offers
from catalog.models import Offer, Product
from normalize.generic import DIMENSION_ORDER

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
        return (row.unit_price, o.price, row.product.title, o.retailer, o.id)

    order = {d: i for i, d in enumerate(DIMENSION_ORDER)}
    groups = [
        MeasureGroup(dimension=dim, rows=sorted(rows, key=row_key), total=len(rows))
        for dim, rows in grouped.items()
    ]
    groups.sort(key=lambda g: (-g.total, order.get(g.dimension, len(order))))
    unknown.sort(key=lambda r: (r.offer.price, r.product.title, r.offer.retailer, r.offer.id))
    return GenericResult(groups=groups, unknown=unknown, total_products=len(by_product))
