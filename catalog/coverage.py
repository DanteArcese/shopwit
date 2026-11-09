"""What the site honestly covers right now, generated from fresh data (never hardcoded)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, F

from .freshness import fresh_observed, fresh_offers
from .geo import Near, bounding_box, within
from .models import Category, Store
from .retailers import display_name

MAX_NEARBY = 8


@dataclass(frozen=True)
class Coverage:
    live: list[str]  # online retailers with fresh live offers
    in_store: list[tuple[str, int]]  # (chain name, stores with fresh recorded prices)


def stores_we_cover(now: datetime) -> Coverage:
    """Two bounded queries: live retailers, and observed chains with their store counts."""
    codes = fresh_offers(now).values_list("retailer", flat=True).distinct()
    live = sorted({display_name(code) for code in codes})
    rows = (
        fresh_observed(now)
        .filter(store__isnull=False)
        .values("store__chain_name")
        .annotate(n=Count("store", distinct=True))
        .order_by("store__chain_name")
    )
    return Coverage(live, [(r["store__chain_name"], r["n"]) for r in rows])


def nearby_categories(now: datetime, near: Near) -> list[tuple[Category, int]]:
    """Active curated categories with fresh recorded prices at stores within `near`.

    Two queries: placed candidate stores (in the box and with fresh recorded prices, via a
    subquery, so empty stores never reach Python), then the per-category counts.
    """
    min_lat, max_lat, min_lon, max_lon = bounding_box(near)
    with_prices = fresh_observed(now).filter(store__isnull=False).values("store_id")
    box = Store.objects.filter(lat__gte=min_lat, lat__lte=max_lat, pk__in=with_prices)
    if min_lon is not None:
        box = box.filter(lon__gte=min_lon, lon__lte=max_lon)
    ids = [pk for pk, lat, lon in box.values_list("pk", "lat", "lon") if within(near, lat, lon)]
    if not ids:
        return []
    counts = dict(
        fresh_observed(now)
        .filter(store_id__in=ids, category__active=True, product__category=F("category"))
        .values_list("category")
        .annotate(n=Count("pk"))
    )
    cats = Category.objects.filter(pk__in=counts, active=True)
    ranked = sorted(((c, counts[c.pk]) for c in cats), key=lambda t: (-t[1], t[0].name))
    return ranked[:MAX_NEARBY]
