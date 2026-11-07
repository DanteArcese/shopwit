"""Deterministic Product view merged from all of its Offers."""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.utils import timezone

from normalize import load_categories
from normalize.compare import unit_prices
from normalize.types import AttrValue

from .models import Product, ReviewItem


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
    """Recompute product fields and ReviewItems from all its offers."""
    offers = list(product.offers.select_related("category"))
    if not offers:
        return
    view = merged_view(offers)
    unresolved = _apply_overrides(product, offers, view)
    product.title = view["title"]
    product.brand = view["brand"]
    product.category = view["category"] or product.category
    product.attributes = view["attributes"]
    product.measures = view["measures"]
    product.save()
    _sync_review_items(product, unresolved, now or timezone.now())


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
    categories = load_categories(settings.CATEGORIES_DIR)
    final = {n: AttrValue(a["value"], a["source"]) for n, a in view["attributes"].items()}
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
    unresolved |= set.intersection(*unit_entries)
    return sorted(unresolved)


def _sync_review_items(product: Product, unresolved: list[str], now: datetime) -> None:
    current = set(unresolved)
    open_items = {i.detail: i for i in product.review_items.filter(status=ReviewItem.Status.OPEN)}
    for detail in sorted(current - open_items.keys()):
        ReviewItem.objects.create(product=product, detail=detail)
    for detail, item in open_items.items():
        if detail not in current:
            item.status = ReviewItem.Status.RESOLVED
            item.resolved_at = now
            item.save(update_fields=["status", "resolved_at", "updated_at"])
