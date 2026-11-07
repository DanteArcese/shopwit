"""Product identity: UPC when it normalizes, else the retailer's own item id."""

from __future__ import annotations

from enrichment.off import normalize_upc
from normalize.types import RawOffer


def product_identity(raw: RawOffer) -> str:
    upc = normalize_upc(raw.upc)
    if upc:
        return f"upc:{upc}"
    return f"{raw.retailer}:{raw.retailer_item_id}"
