"""Open Prices sync: dated shelf prices into Stores, observations and observed Offers.

A failed or partial pull changes nothing: rows missing from the window are
deleted only after the whole window was fetched successfully.
"""

from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import enrichment.off
from enrichment.off import normalize_upc
from connectors.base import ConnectorError
from connectors.open_prices import (
    Observation,
    OpenPricesClient,
    chain_key,
    skip_reason,
    to_raw_offer,
)
from normalize import load_categories, load_store_brands, normalize
from normalize.types import OffProduct

from .ingest import _upsert
from .merge import merge_product
from .models import IngestionRun, Offer, OpenPriceObservation, Product, Store

CONNECTOR = "openprices"


def due(now: datetime) -> bool:
    return not IngestionRun.objects.filter(
        connector=CONNECTOR,
        status=IngestionRun.Status.SUCCEEDED,
        started_at__gte=now - settings.OPEN_PRICES_INTERVAL,
    ).exists()


def _fail(run: IngestionRun, error: str) -> IngestionRun:
    run.status = IngestionRun.Status.FAILED
    run.error = error[:500]
    run.finished_at = timezone.now()
    run.save()
    return run


def _store_fields(obs: Observation) -> dict:
    loc = obs.location
    chain_code, chain_name = chain_key(loc)
    return {
        "name": loc.name,
        "chain_code": chain_code,
        "chain_name": chain_name,
        "city": loc.city,
        "postcode": (loc.postcode or None) and loc.postcode[:20],
        "country_code": (loc.country_code or "").upper()[:2],
        "osm_type": loc.osm_type,
        "osm_id": loc.osm_id,
    }


def sync_open_prices(
    client: OpenPricesClient,
    now: datetime,
    *,
    off_lookup=enrichment.off.lookup,
) -> IngestionRun:
    run = IngestionRun.objects.create(connector=CONNECTOR, category_slug="all", started_at=now)
    since = (now - settings.OBSERVED_MAX_AGE).date()
    try:
        fetched = client.fetch_window(since, settings.SITE_CURRENCY)
    except ConnectorError as exc:
        return _fail(run, str(exc))
    except Exception as exc:  # never leave a RUNNING row; type name only
        return _fail(run, type(exc).__name__)

    today = now.date()
    skipped = client.unparsed
    kept: list[Observation] = []
    for obs in fetched:
        reason = skip_reason(
            obs,
            country=settings.OPEN_PRICES_COUNTRY,
            currency=settings.SITE_CURRENCY,
            include_assisted=settings.OPEN_PRICES_INCLUDE_ASSISTED,
            today=today,
        )
        if reason is None:
            kept.append(obs)
        else:
            skipped += 1
    run.fetched = len(kept)

    # The pull succeeded: store it and drop what vanished, in one transaction.
    stores: dict[str, Store] = {}
    with transaction.atomic():
        for obs in kept:
            code = obs.location.code
            store = stores.get(code)
            if store is None:
                store, _ = Store.objects.update_or_create(code=code, defaults=_store_fields(obs))
                stores[code] = store
            OpenPriceObservation.objects.update_or_create(
                price_id=obs.price_id,
                defaults={
                    "store": store,
                    "upc": normalize_upc(obs.product_code),
                    "product_name": obs.product_name[:500],
                    "brands": obs.brands[:200] if obs.brands else None,
                    "quantity": obs.quantity[:200] if obs.quantity else None,
                    "labels": sorted(obs.labels),
                    "price": obs.price,
                    "currency": obs.currency,
                    "is_sale": obs.is_sale,
                    "observed_on": obs.date,
                    "proof_type": obs.proof_type[:20] if obs.proof_type else None,
                    "source": obs.source[:200] if obs.source else None,
                    "fetched_at": now,
                },
            )
        OpenPriceObservation.objects.exclude(price_id__in=[o.price_id for o in kept]).delete()
        Store.objects.filter(observations__isnull=True).delete()

    # Latest observation per (store, upc) becomes the offer.
    latest: dict[tuple[str, str], Observation] = {}
    for obs in kept:
        key = (obs.location.code, normalize_upc(obs.product_code))
        current = latest.get(key)
        if current is None or (obs.date, obs.price_id) > (current.date, current.price_id):
            latest[key] = obs

    categories = load_categories(settings.CATEGORIES_DIR)
    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)
    model_cache: dict = {}
    touched: set[int] = set()
    live: set[tuple[str, str]] = set()  # (retailer, item id) of offers that must survive
    for (store_code, upc), obs in latest.items():
        raw = to_raw_offer(obs, now)
        live_key = (raw.retailer, raw.retailer_item_id)
        try:
            normalized = normalize(
                raw,
                categories,
                store_brands,
                off=off_lookup(upc) or OffProduct(labels=obs.labels, quantity=obs.quantity),
            )
            if normalized.category is None:
                continue
            live.add(live_key)  # an error below must not delete the existing offer
            with transaction.atomic():
                normalized = _upsert(
                    raw,
                    normalized,
                    model_cache,
                    now,
                    touched,
                    records_price_history=False,
                    extra_defaults={
                        "kind": Offer.Kind.OBSERVED,
                        "observed_on": obs.date,
                        "is_sale": obs.is_sale,
                        "store": stores[store_code],
                    },
                )
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

    with transaction.atomic():
        for offer in Offer.objects.filter(kind=Offer.Kind.OBSERVED):
            if (offer.retailer, offer.retailer_item_id) not in live:
                touched.add(offer.product_id)
                offer.delete()

    for pk in sorted(touched):
        try:
            product = Product.objects.filter(pk=pk).first()
            if product is not None:
                merge_product(product, now)
        except Exception as exc:
            skipped += 1
            if not run.error:
                run.error = f"{type(exc).__name__} merging product {pk}"[:500]

    run.skipped = skipped
    run.status = IngestionRun.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()
    return run
