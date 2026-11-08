"""Ingestion job: connector search results into Products, Offers and ReviewItems."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import enrichment.off
from connectors.base import Connector, ConnectorError
from enrichment.off import normalize_upc
from normalize import load_categories, load_store_brands, normalize
from normalize.generic import generic_measure, generic_unit_price, off_category_path
from normalize.match import brand_tier_from_field
from normalize.types import NormalizedOffer, OffProduct

from .identity import product_identity
from .merge import merge_product
from .models import Category, IngestionRun, Offer, PriceHistory, Product


def _upsert(
    raw,
    normalized: NormalizedOffer,
    model_cache: dict,
    now: datetime,
    touched: set[int],
    *,
    records_price_history: bool,
    extra_defaults: Mapping[str, object] | None = None,
    off: OffProduct | None = None,
    store_brands: frozenset[str] = frozenset(),
) -> NormalizedOffer:
    """Write one offer; adds the affected product ids (new and previous) to ``touched``.

    Products are merged once per run by the caller, after all offers are written.
    """
    category = None
    if normalized.category is not None:
        category = model_cache.get(normalized.category) or Category.objects.get(
            slug=normalized.category
        )
        model_cache[normalized.category] = category
    product, _ = Product.objects.get_or_create(
        identity=product_identity(raw),
        defaults={"title": raw.title, "upc": normalize_upc(raw.upc), "category": category},
    )
    previous = (
        Offer.objects.filter(retailer=raw.retailer, retailer_item_id=raw.retailer_item_id)
        .values_list("product_id", flat=True)
        .first()
    )

    measure = generic_measure(
        [raw.size_text or "", raw.title, (off.quantity if off else None) or ""],
        off.quantity_unit if off else None,
    )
    if category is None:
        tier = brand_tier_from_field(raw.brand, store_brands)
        attributes = {"brand_tier": {"value": tier.value, "source": tier.source}} if tier else {}
        unit_prices: dict[str, str] = {}
        unresolved = ["category"]
    else:
        attributes = {
            name: {"value": a.value, "source": a.source} for name, a in normalized.attributes.items()
        }
        unit_prices = {k: str(v) for k, v in normalized.unit_prices.items()}
        unresolved = list(normalized.unresolved)

    offer, _ = Offer.objects.update_or_create(
        retailer=raw.retailer,
        retailer_item_id=raw.retailer_item_id,
        defaults={
            "product": product,
            "price": raw.price,
            "currency": raw.currency,
            "unit_prices": unit_prices,
            "url": raw.url,
            "last_seen_at": now,
            "category": category,
            "title": raw.title,
            "brand": raw.brand,
            "attributes": attributes,
            "measures": normalized.measures,
            "unresolved": unresolved,
            "labels": sorted(off.labels) if off else [],
            "category_path": raw.fields.get("category_path")
            or off_category_path(off.categories if off else ()),
            "measure_dim": measure.dimension,
            "measure_amount": measure.amount,
            "measure_reason": measure.reason,
            "generic_unit_price": generic_unit_price(raw.price, measure),
            **(extra_defaults or {}),
        },
    )
    if records_price_history:
        last = offer.price_history.order_by("-observed_at", "-id").first()
        if last is None or last.price != raw.price:
            PriceHistory.objects.create(
                offer=offer, price=raw.price, currency=raw.currency, observed_at=now
            )
    touched.add(product.pk)
    if previous is not None and previous != product.pk:
        touched.add(previous)  # the offer moved; the old product must be re-merged
    return normalized


def _fail(run: IngestionRun, error: str, fetched: int, skipped: int) -> IngestionRun:
    run.status = IngestionRun.Status.FAILED
    run.error = error
    run.fetched = fetched
    run.skipped = skipped
    run.finished_at = timezone.now()
    run.save()
    return run


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
        return _fail(run, str(exc), len(unique), skipped)
    except Exception as exc:  # never leave a RUNNING row; the type name only, no message text
        return _fail(run, type(exc).__name__, len(unique), skipped)

    run.fetched = len(unique)
    model_cache: dict[str, Category] = {}
    touched: set[int] = set()
    for raw in unique.values():
        try:
            off = off_lookup(raw.upc)
            normalized = normalize(raw, categories, store_brands, off=off)
            with transaction.atomic():
                normalized = _upsert(
                    raw,
                    normalized,
                    model_cache,
                    now,
                    touched,
                    records_price_history=connector.records_price_history,
                    extra_defaults={"kind": Offer.Kind.LIVE},
                    off=off,
                    store_brands=store_brands,
                )
        except Exception as exc:  # one bad offer must not poison the run
            skipped += 1
            if not run.error:
                run.error = f"{type(exc).__name__} on {raw.retailer}:{raw.retailer_item_id}"[:500]
            continue
        if normalized.category is None:
            continue  # kept as a generic offer; counted in neither matched nor skipped
        run.matched += 1
        if normalized.unresolved:
            run.unresolved += 1
        else:
            run.fully_parsed += 1

    for pk in sorted(touched):
        try:
            merge_product(Product.objects.get(pk=pk), now)
        except Exception as exc:  # one bad product must not fail the run
            skipped += 1
            if not run.error:
                run.error = f"{type(exc).__name__} merging product {pk}"[:500]

    run.skipped = skipped
    run.status = IngestionRun.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()
    return run
