"""Ingestion job: connector search results into Products, Offers and ReviewItems."""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.db import transaction

import enrichment.off
from connectors.base import Connector, ConnectorError
from enrichment.off import normalize_upc
from normalize import load_categories, load_store_brands, normalize
from normalize.types import NormalizedOffer

from . import overrides
from .identity import product_identity
from .merge import merge_product
from .models import Category, IngestionRun, Offer, PriceHistory, Product


def _upsert(connector, raw, normalized: NormalizedOffer, model_cache: dict, now: datetime):
    """Write one offer and refresh its product; returns the (possibly overridden) result."""
    category = model_cache.get(normalized.category) or Category.objects.get(slug=normalized.category)
    model_cache[normalized.category] = category
    product, _ = Product.objects.get_or_create(
        identity=product_identity(raw),
        defaults={"title": raw.title, "upc": normalize_upc(raw.upc), "category": category},
    )
    normalized = overrides.apply(product, normalized)

    offer, _ = Offer.objects.update_or_create(
        retailer=raw.retailer,
        retailer_item_id=raw.retailer_item_id,
        defaults={
            "product": product,
            "price": raw.price,
            "currency": raw.currency,
            "unit_prices": {k: str(v) for k, v in normalized.unit_prices.items()},
            "url": raw.url,
            "last_seen_at": now,
            "category": category,
            "title": raw.title,
            "brand": raw.brand,
            "attributes": {
                name: {"value": a.value, "source": a.source}
                for name, a in normalized.attributes.items()
            },
            "measures": normalized.measures,
            "unresolved": list(normalized.unresolved),
        },
    )
    if connector.records_price_history:
        last = offer.price_history.order_by("-observed_at", "-id").first()
        if last is None or last.price != raw.price:
            PriceHistory.objects.create(
                offer=offer, price=raw.price, currency=raw.currency, observed_at=now
            )
    merge_product(product, now)
    return normalized


def ingest(
    connector: Connector,
    category: Category,
    now: datetime,
    *,
    off_lookup=enrichment.off.lookup,
) -> IngestionRun:
    run = IngestionRun.objects.create(
        connector=connector.name, category_slug=category.slug, started_at=now
    )
    categories = load_categories(settings.CATEGORIES_DIR)
    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)

    unique = {}
    skipped = 0
    try:
        for query in category.search:
            result = connector.search(query)
            skipped += result.skipped
            for raw in result.offers:
                unique.setdefault((raw.retailer, raw.retailer_item_id), raw)
    except ConnectorError as exc:
        run.status = IngestionRun.Status.FAILED
        run.error = str(exc)
        run.fetched = len(unique)
        run.skipped = skipped
        run.finished_at = now
        run.save()
        return run

    run.fetched = len(unique)
    model_cache: dict[str, Category] = {}
    for raw in unique.values():
        try:
            normalized = normalize(raw, categories, store_brands, off=off_lookup(raw.upc))
            if normalized.category is None:
                continue
            with transaction.atomic():
                normalized = _upsert(connector, raw, normalized, model_cache, now)
        except Exception as exc:  # one bad offer must not poison the run
            skipped += 1
            if not run.error:
                run.error = f"{type(exc).__name__} on {raw.retailer}:{raw.retailer_item_id}"[:500]
            continue
        run.matched += 1
        if normalized.unresolved:
            run.unresolved += 1
        else:
            run.fully_parsed += 1

    run.skipped = skipped
    run.status = IngestionRun.Status.SUCCEEDED
    run.finished_at = now
    run.save()
    return run
