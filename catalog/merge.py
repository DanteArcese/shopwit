"""Deterministic Product view merged from all of its Offers.

Unit prices are computed from the merged product attributes, not each offer's
own: offers share a Product only through identity (a UPC, or the retailer's own
item id), and the product carries one attribute set (plus manual overrides) that
every offer's unit price is built from.

Ambiguity is handled asymmetrically on purpose. Disagreeing attribute or
quantity values across offers are omitted and flagged as a conflict, never
guessed. An unresolved ``unit_price:*`` entry, however, is reported for the
product only when it is unresolved on every offer, because one computable offer
is enough to compare the product; a single offer's missing price stays on that
offer alone.
"""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from normalize import load_categories
from normalize.compare import unit_prices
from normalize.types import AttrValue

from .models import Offer, Product, ReviewItem


def _merge_map(maps: list[dict], conflict_entry) -> tuple[dict, list[str]]:
    merged: dict = {}
    conflicts: list[str] = []
    for key in sorted({k for m in maps for k in m}):
        present = [m[key] for m in maps if key in m]
        values = {repr(v["value"] if isinstance(v, dict) else v) for v in present}
        if len(values) == 1:
            merged[key] = present[0]
        else:
            conflicts.append(conflict_entry(key))
    return merged, conflicts


def merged_view(offers: list) -> dict:
    """Pure merge over offers (objects with stored normalization fields)."""
    offers = sorted(offers, key=lambda o: (o.retailer, o.retailer_item_id))
    first = offers[0]
    attributes, attr_conflicts = _merge_map(
        [o.attributes for o in offers], lambda k: f"attribute:{k}:conflict"
    )
    measures, qty_conflicts = _merge_map(
        [o.measures for o in offers], lambda k: "quantity:conflict"
    )
    unresolved: set[str] = set()
    for o in offers:
        unresolved.update(e for e in o.unresolved if not e.startswith("unit_price:"))
    unit_entries = set.intersection(
        *[{e for e in o.unresolved if e.startswith("unit_price:")} for o in offers]
    )
    unresolved |= unit_entries
    unresolved = {
        e for e in unresolved
        if not (e.startswith("attribute:") and e[len("attribute:"):] in attributes)
        and not (e == "quantity" and measures)
    }
    unresolved.update(attr_conflicts)
    unresolved.update(qty_conflicts)
    return {
        "title": first.title,
        "brand": first.brand,
        "category": first.category,
        "attributes": attributes,
        "measures": measures,
        "unresolved": sorted(unresolved),
    }


def merge_product(product: Product, now: datetime | None = None) -> None:
    """Recompute product fields and ReviewItems from all its offers.

    Runs under a row lock on a freshly fetched Product, so a caller holding a
    stale instance cannot overwrite a concurrent manual_attributes edit: only
    derived columns are written, never manual_attributes. With no offers left,
    the product's open ReviewItems are resolved.
    """
    now = now or timezone.now()
    with transaction.atomic():
        locked = Product.objects.select_for_update().get(pk=product.pk)
        offers = list(locked.offers.select_related("category"))
        if not offers:
            _sync_review_items(locked, [], now)
            return
        # Observed (in-store) offers never change live data: with any live offer,
        # the merge, conflicts, review items and unit prices use live offers only.
        live = [o for o in offers if o.kind == Offer.Kind.LIVE]
        basis = live or offers
        view = merged_view(basis)
        unresolved = _apply_overrides(locked, basis, view)
        if live:
            # Observed offers stay out of the merge but price from the final attributes.
            _refresh_unit_prices(
                [o for o in offers if o.kind != Offer.Kind.LIVE], view["attributes"]
            )
        else:
            unresolved = []  # in-store prices alone never open review items
        locked.title = view["title"]
        locked.brand = view["brand"]
        locked.category = view["category"] or locked.category
        locked.attributes = view["attributes"]
        locked.measures = view["measures"]
        locked.save(
            update_fields=["title", "brand", "category", "attributes", "measures", "updated_at"]
        )
        _sync_review_items(locked, unresolved, now)
    for field in ("title", "brand", "category", "attributes", "measures", "manual_attributes", "updated_at"):
        setattr(product, field, getattr(locked, field))


def _apply_overrides(product: Product, offers: list, view: dict) -> list[str]:
    """Overlay manual attributes on the merged view and refresh offers' unit prices.

    Mutates ``view["attributes"]``; returns the final unresolved list. Offers'
    unit prices and ``unit_price:*`` entries are always rebuilt from their own
    measures plus the product's final attributes, so removing an override
    restores them.
    """
    unresolved = {e for e in view["unresolved"] if not e.startswith("unit_price:")}
    for name, value in product.manual_attributes.items():
        view["attributes"][name] = {"value": value, "source": "manual"}
        unresolved = {
            e for e in unresolved if e != f"attribute:{name}" and not e.startswith(f"attribute:{name}:")
        }
    unit_entries = _refresh_unit_prices(offers, view["attributes"])
    unresolved |= set.intersection(*unit_entries)
    return sorted(unresolved)


def _refresh_unit_prices(offers: list, attributes: dict) -> list[set[str]]:
    """Rebuild each offer's unit prices from its measures plus the final attributes."""
    categories = load_categories(settings.CATEGORIES_DIR)
    final = {n: AttrValue(a["value"], a["source"]) for n, a in attributes.items()}
    unit_entries = []
    for offer in offers:
        category = categories.get(offer.category.slug) if offer.category else None
        if category is not None:
            prices = {
                k: str(v) for k, v in unit_prices(offer.price, category, offer.measures, final).items()
            }
            entries = [] if category.comparison_default in prices else [f"unit_price:{category.comparison_default}"]
            rebuilt = [e for e in offer.unresolved if not e.startswith("unit_price:")] + entries
            if (prices, rebuilt) != (offer.unit_prices, offer.unresolved):
                offer.unit_prices, offer.unresolved = prices, rebuilt
                offer.save(update_fields=["unit_prices", "unresolved", "updated_at"])
        unit_entries.append({e for e in offer.unresolved if e.startswith("unit_price:")})
    return unit_entries


def _sync_review_items(product: Product, unresolved: list[str], now: datetime) -> None:
    current = set(unresolved)
    # User reports stay open until an admin resolves them.
    open_items = {
        i.detail: i
        for i in product.review_items.filter(
            status=ReviewItem.Status.OPEN, reason=ReviewItem.Reason.INGEST
        )
    }
    for detail in sorted(current - open_items.keys()):
        # get_or_create: a concurrent writer may have opened the same item.
        ReviewItem.objects.get_or_create(
            product=product, detail=detail, status=ReviewItem.Status.OPEN
        )
    for detail, item in open_items.items():
        if detail not in current:
            item.status = ReviewItem.Status.RESOLVED
            item.resolved_at = now
            item.save(update_fields=["status", "resolved_at", "updated_at"])
