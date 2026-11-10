"""Ingestion job: connector search results into Products, Offers and ReviewItems."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import enrichment.off
from connectors.base import Connector, ConnectorError
from connectors.live import BudgetExhausted, LiveSearchResult, Stopping
from enrichment.off import normalize_upc
from normalize import load_categories, load_store_brands, normalize
from normalize.generic import generic_measure, generic_unit_price, off_category_path
from normalize.match import brand_tier_from_field
from normalize.nutrition import offer_nutrition
from normalize.types import NormalizedOffer, OffProduct

from .identity import product_identity
from .merge import merge_product
from .models import Category, IngestionRun, Offer, PriceHistory, Product

if TYPE_CHECKING:
    from .budget import DbBudget

logger = logging.getLogger(__name__)

INTERRUPTED = "interrupted"  # IngestionRun.error for a run cut short by a worker shutdown


def _shipping(text: str | None) -> Decimal | None:
    """The stored shipping, or None (also clears a stale value) when absent or unusable."""
    if text is None:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() and 0 <= value < 10**6 else None


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
            "shipping": _shipping(raw.fields.get("shipping")),
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
            "nutrition": offer_nutrition(
                raw.price,
                off.nutrition if off else None,
                normalized.measures,
                measure.dimension,
                measure.amount,
            ),
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
    budget: DbBudget | None = None,
) -> IngestionRun:
    """Refresh one category. With ``budget``, a budgeted connector waits for a token per search."""
    run = IngestionRun.objects.create(
        connector=connector.name, category_slug=category.slug, started_at=now
    )
    categories = load_categories(settings.CATEGORIES_DIR)
    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)

    unique = {}
    skipped = 0
    paced = budget is not None and connector.name in settings.SOURCE_BUDGETS
    try:
        for query in category.search:
            if paced:
                budget.wait(connector.name)
            result = connector.search(query)
            skipped += result.skipped
            for raw in result.offers:
                unique.setdefault((raw.retailer, raw.retailer_item_id), raw)
    except ConnectorError as exc:
        return _fail(run, str(exc), len(unique), skipped)
    except Stopping:  # the worker is shutting down; not a source or budget problem
        return _fail(run, INTERRUPTED, len(unique), skipped)
    except BudgetExhausted:  # the next tick retries
        return _fail(run, "budget exhausted", len(unique), skipped)
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


def ingest_live(
    result: LiveSearchResult,
    source_name: str,
    now: datetime,
    *,
    off_lookup=enrichment.off.lookup,
) -> int:
    """Write one live search result; returns the number of distinct products touched.

    OFF records go to the ``OffEntry`` cache first (so the offers below see them),
    live offers are written curated or generic and never to ``PriceHistory``, and
    Open Prices observations are written exactly as the daily sync writes them.
    One bad record is skipped, never fatal.
    """
    from connectors.open_prices import skip_reason

    from .observed import upsert_observations  # observed imports this module

    categories = load_categories(settings.CATEGORIES_DIR)
    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)
    skipped = 0
    for record in result.off_records:
        try:
            enrichment.off._save(record)
        except Exception:
            skipped += 1

    model_cache: dict[str, Category] = {}
    touched: set[int] = set()
    for raw in result.offers:
        try:
            off = off_lookup(raw.upc)
            normalized = normalize(raw, categories, store_brands, off=off)
            with transaction.atomic():
                _upsert(
                    raw,
                    normalized,
                    model_cache,
                    now,
                    touched,
                    records_price_history=False,
                    extra_defaults={"kind": Offer.Kind.LIVE},
                    off=off,
                    store_brands=store_brands,
                )
        except Exception:
            skipped += 1

    today = now.date()
    kept = [
        obs
        for obs in result.observations
        if skip_reason(
            obs,
            country=settings.OPEN_PRICES_COUNTRY,
            currency=settings.SITE_CURRENCY,
            include_assisted=settings.OPEN_PRICES_INCLUDE_ASSISTED,
            today=today,
        )
        is None
    ]
    skipped += len(result.observations) - len(kept)
    if kept:
        try:
            observed_touched, _, observed_skipped = upsert_observations(
                kept, now, off_lookup=off_lookup, run=None
            )
            touched |= observed_touched
            skipped += observed_skipped
        except Exception as exc:  # the observation transaction rolled back; offers above stay
            skipped += len(kept)
            logger.warning("ingest_live source=%s observations failed: %s", source_name, type(exc).__name__)

    for pk in sorted(touched):
        try:
            merge_product(Product.objects.get(pk=pk), now)
        except Exception:
            skipped += 1
    if skipped:
        logger.info("ingest_live source=%s skipped=%s", source_name, skipped + result.skipped)
    return len(touched)
