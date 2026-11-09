"""Recorded Walmart sample data: identify and purge it from a database."""

import json
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction

from catalog.models import Offer, Product


def sample_item_ids(directory: Path) -> set[str]:
    """Every json["items"][*]["itemId"] across the recordings in DIRECTORY, as str."""
    directory = Path(directory)
    if not directory.is_dir():
        raise CommandError(f"Recordings directory not found: {directory}")
    ids: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        body = json.loads(path.read_text()).get("json")
        items = body.get("items") if isinstance(body, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("itemId") is not None:
                ids.add(str(item["itemId"]))
    if not ids:
        raise CommandError(f"No item ids found in {directory}")
    return ids


def purge_sample(ids: set[str], apply: bool) -> tuple[int, int]:
    """Delete live Walmart offers whose item id is in IDS, then products left with no offers.

    Returns (offers, products) counts; with apply=False nothing is deleted. Products that
    still have any other offer (a real offer or an Open Prices observation) are kept.
    Deleting a product cascades its review items and price history, and unpins it from
    account list items (ListItem.pinned_product is SET_NULL). Session-stored pins hold a
    bare product id and simply stop resolving ("no current price"). IngestionRun rows stay.
    """
    with transaction.atomic():
        offers = Offer.objects.filter(
            retailer="walmart", kind=Offer.Kind.LIVE, retailer_item_id__in=ids
        )
        offer_pks = list(offers.values_list("pk", flat=True))
        product_ids = set(offers.values_list("product_id", flat=True))
        orphaned = Product.objects.filter(pk__in=product_ids).exclude(
            offers__in=Offer.objects.exclude(pk__in=offer_pks)
        )
        n_products = orphaned.count()
        if apply:
            product_pks = list(orphaned.values_list("pk", flat=True))
            offers.delete()
            Product.objects.filter(pk__in=product_pks).delete()
        return len(offer_pks), n_products
