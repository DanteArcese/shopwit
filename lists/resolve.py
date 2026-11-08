"""Resolve list specs to the cheapest fresh live offers at the visitor's stores.

Honest ranking: every choice depends only on price, quantity, the spec and the stores.
Ties break on price, then quantity fit, then product id, then offer id.
Only live offers from ``fresh_offers`` take part; observed (in-store) prices never do.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.conf import settings

from catalog.browse import (
    BRAND_TIERS,
    matching_offers,
    parse_filters_strict,
)
from catalog.fetch import enqueue
from catalog.freshness import fresh_offers
from catalog.generic import (
    SIZE_PARAMS,
    _bound,
    generic_matching_offers,
    load_label_allowlist,
)
from catalog.models import Category, FetchJob
from catalog.retailers import available_retailers, display_name, is_chain_code
from connectors.registry import live_source_info
from normalize.generic import normalize_phrase
from normalize.text import fold

from .basket import (
    Split,
    StoreTotal,
    cheapest_single_store,
    cheapest_split,
    store_totals,
)
from .fit import Fit, fit_offer, package_size
from .spec import ItemSpec, ListSpec

logger = logging.getLogger(__name__)

MAX_ALTERNATIVES = 2

GONE = "This category is no longer available"
PIN_GONE = "The pinned product has no current price at your stores"
NO_PRICES = "No current prices match at your stores"
SIZE_UNKNOWN = "Size unknown for every match"
TOO_MANY = "Needs more than 24 packages"
MIXED = "Can't total: prices in more than one currency"

_SIZE_FACETS = {
    f"{key}_{end}": dimension
    for dimension, key in SIZE_PARAMS.items()
    for end in ("min", "max")
}


def invalid_reason(keys) -> str:
    return "Filter no longer valid: " + ", ".join(keys)


@dataclass(frozen=True)
class ItemResolution:
    item: ItemSpec
    category: Category | None
    by_store: Mapping[str, Fit]
    best: Fit | None
    alternatives: tuple[Fit, ...]
    reason: str | None


@dataclass(frozen=True)
class ListResolution:
    items: list[ItemResolution]
    stores: tuple[str, ...]
    totals: list[StoreTotal]
    single: StoreTotal | None
    split: Split | None
    max_split_stores: int | None
    currency: str | None
    mixed_currency: bool
    oldest_price_at: datetime | None
    # Items with no fit anywhere: left out of every total (each has its own reason).
    unresolved: tuple[str, ...] = ()


def choice_key(fit: Fit) -> tuple:
    """Price, then quantity fit, then product id, then offer id. Nothing else."""
    return (
        fit.total_price,
        fit.surplus,
        fit.packages,
        fit.offer.product_id,
        fit.offer.pk,
    )


def _unresolved(item, category, reason) -> ItemResolution:
    return ItemResolution(item, category, {}, None, (), reason)


def invalid_facets(
    facets: Mapping[str, tuple[str, ...]], unit_system: str
) -> tuple[str, ...]:
    """Sorted facet keys that are unknown, or whose stored value no longer parses."""
    allowlist = load_label_allowlist(settings.LABELS_FILE)
    bad = []
    for key, values in facets.items():
        values = tuple(values)
        if not values:
            ok = False
        elif key == "brand":
            ok = all(fold(v) for v in values)
        elif key == "label":
            ok = all(v in allowlist for v in values)
        elif key == "brand_tier":
            ok = len(values) == 1 and values[0] in BRAND_TIERS
        elif key == "path":
            ok = len(values) == 1 and bool(values[0].strip().strip("/"))
        elif key in _SIZE_FACETS:
            ok = (
                len(values) == 1
                and _bound(values[0], _SIZE_FACETS[key], unit_system) is not None
            )
        else:
            ok = False
        if not ok:
            bad.append(key)
    return tuple(sorted(bad))


def _candidates(item: ItemSpec, stores: frozenset[str], now: datetime, cache: dict):
    """(category, candidate offers, reason). A reason means the item cannot resolve."""
    if item.kind == "generic":
        dimension = item.target_measure
        bad = invalid_facets(item.facets, item.unit_system)
        if bad:
            return None, [], invalid_reason(bad)
        if item.pinned_product is not None:
            qs = fresh_offers(now).filter(
                product_id=item.pinned_product,
                measure_dim=dimension,
                retailer__in=stores,
            )
            return None, list(qs.defer("search_vector").select_related("product", "store")), None
        offers = generic_matching_offers(
            item.query,
            {k: list(v) for k, v in item.facets.items()},
            dimension,
            stores,
            now,
            unit_system=item.unit_system,
        )
        # Belt and braces: never an offer outside the item's dimension.
        return None, [o for o in offers if o.measure_dim == dimension], None

    if item.category not in cache:
        cache[item.category] = (
            Category.objects.filter(slug=item.category, active=True)
            .prefetch_related("attribute_defs")
            .first()
        )
    category = cache[item.category]
    if category is None:
        return None, [], GONE
    spec, bad = parse_filters_strict(category, item.filters)
    if bad:
        return category, [], invalid_reason(bad)
    if item.pinned_product is not None:
        # The pin is the user's explicit choice: filters are not applied; category,
        # freshness and stores are.
        qs = fresh_offers(now).filter(
            product_id=item.pinned_product,
            product__category=category,
            category=category,
            retailer__in=stores,
        )
        return category, list(qs.defer("search_vector").select_related("product", "store")), None
    return category, matching_offers(category, spec, stores, now), None


def resolve_item(
    item: ItemSpec, stores: frozenset[str], now: datetime, *, _cache: dict | None = None
) -> ItemResolution:
    category, candidates, reason = _candidates(
        item, stores, now, {} if _cache is None else _cache
    )
    if reason is not None:
        return _unresolved(item, category, reason)

    fits = [
        f
        for o in candidates
        if (f := fit_offer(o, item.target_measure, item.target_amount))
    ]
    by_store: dict[str, Fit] = {}
    for fit in sorted(fits, key=choice_key):
        by_store.setdefault(fit.offer.retailer, fit)
    if not by_store:
        if item.pinned_product is not None and not candidates:
            why = PIN_GONE
        elif not candidates:
            why = NO_PRICES
        elif not any(
            (size := package_size(o, item.target_measure)) is not None and size > 0
            for o in candidates
        ):
            why = SIZE_UNKNOWN
        else:
            why = TOO_MANY
        return _unresolved(item, category, why)

    best = min(by_store.values(), key=choice_key)
    per_size: dict[Decimal, Fit] = {}
    for fit in sorted(fits, key=choice_key):
        if (
            fit.offer.retailer == best.offer.retailer
            and fit.package_size != best.package_size
        ):
            per_size.setdefault(fit.package_size, fit)
    alternatives = tuple(sorted(per_size.values(), key=choice_key)[:MAX_ALTERNATIVES])
    return ItemResolution(item, category, by_store, best, alternatives, None)


MAX_FETCH_PHRASES = 10


def queue_fetches(spec: ListSpec, now: datetime, client_key: str | None) -> int:
    """Queue a live fetch per distinct generic phrase (at most MAX_FETCH_PHRASES).

    Called by the list page's full GET only, never by resolution. Phrases with a
    succeeded fetch inside the source's refresh window are skipped; the rest go through
    the normal dedup and budgets. Best effort: returns the number of phrases submitted.
    """
    phrases = sorted(
        {
            p
            for i in spec.items
            if i.kind == "generic"
            if (p := normalize_phrase(i.query))
        }
    )
    if not phrases:
        return 0
    try:
        sources = live_source_info(live_off=settings.LIVE_OFF_ENABLED)
        if not sources:
            return 0
        due = []
        for phrase in phrases:
            fresh = {
                name
                for name, info in sources.items()
                if FetchJob.objects.filter(
                    source=name,
                    phrase=phrase,
                    status=FetchJob.Status.SUCCEEDED,
                    finished_at__gte=now - info.refresh_after,
                ).exists()
            }
            if fresh != set(sources):
                due.append(phrase)
        for phrase in due[:MAX_FETCH_PHRASES]:
            enqueue(phrase, "list", now, sources, client_key=client_key)
        return len(due[:MAX_FETCH_PHRASES])
    except Exception as exc:  # best effort; the type name only, never the phrase
        logger.error("list fetch not queued: %s", type(exc).__name__)
        return 0


def _live_stores(stores: frozenset[str] | None, now: datetime) -> tuple[str, ...]:
    codes = available_retailers(now) if stores is None else stores
    # Observed (in-store) chains never enter lists: only live offers are resolved.
    live = {c for c in codes if not is_chain_code(c)}
    return tuple(sorted(live, key=lambda c: (display_name(c), c)))


def resolve_list(
    spec: ListSpec,
    stores: frozenset[str] | None,
    now: datetime,
    max_split_stores: int | None = 2,
) -> ListResolution:
    ordered = _live_stores(stores, now)
    allowed = frozenset(ordered)
    cache: dict = {}
    items = [resolve_item(i, allowed, now, _cache=cache) for i in spec.items]

    resolved = [r for r in items if r.best is not None]
    unresolved = tuple(r.item.id for r in items if r.best is None)
    costs = {
        r.item.id: {s: f.total_price for s, f in r.by_store.items()} for r in resolved
    }
    currencies = {f.offer.currency for r in resolved for f in r.by_store.values()}

    empty = ListResolution(
        items=items,
        stores=ordered,
        totals=[],
        single=None,
        split=None,
        max_split_stores=max_split_stores,
        currency=None,
        mixed_currency=len(currencies) > 1,
        oldest_price_at=None,
        unresolved=unresolved,
    )
    if not costs or len(currencies) != 1:
        return empty

    totals = store_totals(costs, ordered)
    single = cheapest_single_store(totals)
    split = cheapest_split(costs, ordered, max_split_stores)
    fits = {r.item.id: r.by_store for r in resolved}
    used: list[datetime] = []
    if single is not None:
        used += [fits[i][single.store].offer.last_seen_at for i in single.covered]
    if split is not None:
        used += [fits[i][s].offer.last_seen_at for i, s in split.assignments.items()]
    return ListResolution(
        items=items,
        stores=ordered,
        totals=totals,
        single=single,
        split=split,
        max_split_stores=max_split_stores,
        currency=next(iter(currencies)),
        mixed_currency=False,
        oldest_price_at=min(used, default=None),
        unresolved=unresolved,
    )
