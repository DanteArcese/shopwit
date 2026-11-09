"""Recorded Walmart sample data: identify and purge it from a database."""

import json
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from catalog.merge import merge_product
from catalog.models import Offer, Product


def _norm(text: object) -> str:
    return " ".join(str(text or "").casefold().split())


def sample_items(directory: Path) -> dict[str, dict[str, set[str]]]:
    """Map each recorded itemId (str) to its normalized names and canonical/product URLs."""
    directory = Path(directory)
    if not directory.is_dir():
        raise CommandError(f"Recordings directory not found: {directory}")
    found: dict[str, dict[str, set[str]]] = {}
    for path in sorted(directory.glob("*.json")):
        body = json.loads(path.read_text()).get("json")
        items = body.get("items") if isinstance(body, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("itemId") is None:
                continue
            item_id = str(item["itemId"])
            entry = found.setdefault(item_id, {"names": set(), "urls": set()})
            entry["names"].add(_norm(item.get("name")))
            entry["urls"].add(f"https://www.walmart.com/ip/{item_id}")
            if item.get("productUrl"):
                entry["urls"].add(_norm(item["productUrl"]))
    if not found:
        raise CommandError(f"No item ids found in {directory}")
    return found


def sample_item_ids(directory: Path) -> set[str]:
    """Every json["items"][*]["itemId"] across the recordings in DIRECTORY, as str."""
    return set(sample_items(directory))


def _is_sample(offer: Offer, entry: dict[str, set[str]]) -> bool:
    return _norm(offer.title) in entry["names"] or _norm(offer.url) in entry["urls"]


def purge_sample(items: dict[str, dict[str, set[str]]], apply: bool) -> tuple[int, int, int, int]:
    """Delete recorded Walmart sample offers; return (offers, products, pins, remerged).

    An offer is sample only if it is a live walmart offer whose item id is in ITEMS and
    whose title or URL matches the recorded item (so a real offer with a colliding id is
    kept). Products left with no offer are deleted (cascading review items and price
    history; ListItem.pinned_product is SET_NULL so account items are unpinned, counted
    as pins; session pins stop resolving). Surviving products that lost a sample offer
    are re-merged so no sample title/brand/attributes remain. IngestionRun rows stay.
    With apply=False nothing is changed.
    """
    from lists.models import ListItem

    with transaction.atomic():
        candidates = Offer.objects.filter(
            retailer="walmart", kind=Offer.Kind.LIVE, retailer_item_id__in=list(items)
        )
        offer_pks = [o.pk for o in candidates if _is_sample(o, items[o.retailer_item_id])]
        offers = Offer.objects.filter(pk__in=offer_pks)
        product_ids = set(offers.values_list("product_id", flat=True))
        orphaned = set(
            Product.objects.filter(pk__in=product_ids)
            .exclude(offers__in=Offer.objects.exclude(pk__in=offer_pks))
            .values_list("pk", flat=True)
        )
        survivors = product_ids - orphaned
        pins = ListItem.objects.filter(pinned_product_id__in=orphaned).count()
        if apply:
            offers.delete()
            Product.objects.filter(pk__in=orphaned).delete()
            now = timezone.now()
            for product in Product.objects.filter(pk__in=survivors):
                merge_product(product, now)
        return len(offer_pks), len(orphaned), pins, len(survivors)
