"""Unit-price outlier guard (#127).

A unit price far below what a category's other offers cost per unit almost always
means a wrong size upstream (Open Food Facts listing "115 oz" for an 11.5 oz bag).
Such an offer is held from ranking for that comparison until the data changes or
staff approve it as a real price. Nothing about the offer itself is modified.

Rule, per active category and per comparison it allows, over the offers ``browse``
would consider fresh (live and observed) that have a unit price for it: with at
least ``MIN_OFFERS`` such offers, an offer is an outlier when its unit price is
below median / ``RATIO``. Low side only.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .freshness import fresh_observed, fresh_offers
from .models import Category, Offer, ReviewItem

logger = logging.getLogger(__name__)

MIN_OFFERS = 5
RATIO = Decimal(10)
DETAIL_PREFIX = "outlier:"
# Why a held offer is unranked: by unit price, by nutrition cost and in generic search.
HELD_REASON = "size looks wrong (held for review)"


def median(values: list[Decimal]) -> Decimal:
    """The middle value; for an even count, the mean of the two middle values."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def find_outliers(prices: Mapping[Hashable, Decimal]) -> tuple[Decimal | None, set]:
    """(median, keys whose price is below median / RATIO); (None, set()) under MIN_OFFERS."""
    if len(prices) < MIN_OFFERS:
        return None, set()
    m = median(list(prices.values()))
    cutoff = m / RATIO
    return m, {k for k, p in prices.items() if p < cutoff}


def detail_for(comparison: str, offer_id: int) -> str:
    return f"{DETAIL_PREFIX}{comparison}:offer={offer_id}"


def parse_detail(detail: str) -> tuple[str, int] | None:
    """(comparison, offer id) from a ``detail_for`` string, else None."""
    if not detail.startswith(DETAIL_PREFIX):
        return None
    comparison, sep, offer = detail[len(DETAIL_PREFIX) :].partition(":offer=")
    if not sep or not comparison or not offer.isdigit():
        return None
    return comparison, int(offer)


def _price(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def flag_unit_price_outliers(now: datetime) -> dict[str, dict[str, int]]:
    """Store which offers are held, open/resolve their ReviewItems; returns held counts.

    Stats: active category slug -> {comparison: number of offers held}. Idempotent:
    a second run with the same data writes nothing.
    """
    stats: dict[str, dict[str, int]] = {}
    wanted: dict[int, dict[str, str]] = {}  # offer id -> {comparison: median}
    products: dict[int, int] = {}  # offer id -> product id
    for category in Category.objects.filter(active=True).order_by("slug"):
        comparisons = [category.comparison_default, *category.comparison_alternates]
        rows = list(
            (fresh_offers(now) | fresh_observed(now))
            .filter(category=category, product__category=category)
            .values_list("pk", "product_id", "unit_prices", "approved_comparisons")
        )
        counts: dict[str, int] = {}
        for comparison in dict.fromkeys(comparisons):
            prices = {}
            for pk, _, unit_prices, _ in rows:
                value = _price((unit_prices or {}).get(comparison))
                if value is not None:
                    prices[pk] = value
            m, low = find_outliers(prices)
            count = 0
            for pk, product_id, _, approved in rows:
                if pk in low and comparison not in (approved or []):
                    wanted.setdefault(pk, {})[comparison] = str(m)
                    products[pk] = product_id
                    count += 1
            counts[comparison] = count
        stats[category.slug] = counts

    with transaction.atomic():
        current = dict(
            Offer.objects.exclude(held_comparisons={}).values_list(
                "pk", "held_comparisons"
            )
        )
        for pk in current.keys() - wanted.keys():
            Offer.objects.filter(pk=pk).update(held_comparisons={})
        for pk, held in wanted.items():
            if current.get(pk) != held:
                Offer.objects.filter(pk=pk).update(held_comparisons=held)
        _sync_review_items(wanted, products, now)
    return stats


def _sync_review_items(
    wanted: dict[int, dict[str, str]], products: dict[int, int], now: datetime
) -> None:
    want = {
        (products[pk], detail_for(c, pk)) for pk, held in wanted.items() for c in held
    }
    open_items = ReviewItem.objects.filter(
        reason=ReviewItem.Reason.OUTLIER, status=ReviewItem.Status.OPEN
    )
    have = set()
    for item in open_items:
        key = (item.product_id, item.detail)
        if key in want:
            have.add(key)
        else:
            item.status = ReviewItem.Status.RESOLVED
            item.resolved_at = now
            item.save(update_fields=["status", "resolved_at", "updated_at"])
    for product_id, detail in sorted(want - have):
        ReviewItem.objects.get_or_create(
            product_id=product_id,
            detail=detail,
            status=ReviewItem.Status.OPEN,
            defaults={"reason": ReviewItem.Reason.OUTLIER},
        )


def flag_outliers_safely(now: datetime) -> dict[str, dict[str, int]] | None:
    """Run the pass, logging (type name only) instead of raising: callers are ingest jobs."""
    try:
        return flag_unit_price_outliers(now)
    except Exception as exc:
        logger.error("unit price outlier pass failed: %s", type(exc).__name__)
        return None


def approve(offer: Offer, comparison: str) -> None:
    """Mark ``offer``'s unit price for ``comparison`` as real: never held for it again."""
    with transaction.atomic():
        locked = Offer.objects.select_for_update().get(pk=offer.pk)
        approved = list(locked.approved_comparisons or [])
        if comparison not in approved:
            approved.append(comparison)
        held = {
            c: m for c, m in (locked.held_comparisons or {}).items() if c != comparison
        }
        Offer.objects.filter(pk=locked.pk).update(
            approved_comparisons=approved, held_comparisons=held
        )
