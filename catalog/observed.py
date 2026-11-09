"""Open Prices sync: dated shelf prices into Stores, observations and observed Offers.

A failed or partial pull changes nothing: rows missing from the window are
deleted only after the whole window was fetched successfully.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

import enrichment.off
from connectors.base import ConnectorError
from connectors.live import BudgetExhausted, Stopping
from connectors.open_prices import (
    Observation,
    OpenPricesClient,
    chain_key,
    skip_reason,
    to_raw_offer,
)
from enrichment.off import normalize_upc
from normalize import load_categories, load_store_brands, normalize
from normalize.types import OffProduct

from .ingest import INTERRUPTED, _upsert
from .merge import merge_product
from .models import IngestionRun, Offer, OpenPriceObservation, Product, Store

CONNECTOR = "openprices"


RETRY_BACKOFF = timedelta(hours=1)
MIN_KEPT_RATIO = 0.5  # a pull smaller than this share of the stored rows deletes nothing

logger = logging.getLogger(__name__)


def observed_off_product(
    entry: OffProduct | None, labels, quantity: str | None, categories
) -> OffProduct:
    """OFF data for an observed offer: the cached entry's value per field, else Open Prices'.

    A 404 placeholder entry (no labels, no quantity) must not hide what Open Prices sent.
    """
    if entry is not None and entry.quantity:
        quantity, unit = entry.quantity, entry.quantity_unit
    else:
        unit = None
    return OffProduct(
        labels=frozenset(entry.labels) if entry is not None and entry.labels else frozenset(labels),
        quantity=quantity,
        categories=tuple(entry.categories)
        if entry is not None and entry.categories
        else tuple(categories),
        quantity_unit=unit,
        nutrition=entry.nutrition if entry is not None else None,
    )


def due(now: datetime) -> bool:
    runs = IngestionRun.objects.filter(connector=CONNECTOR)
    # A run interrupted by a shutdown says nothing about upstream, so it never delays a retry.
    recent = runs.filter(started_at__gte=now - RETRY_BACKOFF).exclude(
        status=IngestionRun.Status.FAILED, error=INTERRUPTED
    )
    if recent.exists():
        return False  # any run, so a failing upstream is retried hourly at most
    return not runs.filter(
        status=IngestionRun.Status.SUCCEEDED,
        started_at__gte=now - settings.OPEN_PRICES_INTERVAL,
    ).exists()


def _fail(run: IngestionRun, error: str) -> IngestionRun:
    run.status = IngestionRun.Status.FAILED
    run.error = error[:500]
    run.finished_at = timezone.now()
    run.save()
    return run


def _clip(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    return value[: Store._meta.get_field(field).max_length]


def _store_fields(obs: Observation) -> dict:
    loc = obs.location
    chain_code, chain_name = chain_key(loc)
    return {
        "name": _clip("name", loc.name),
        "chain_code": chain_code,
        "chain_name": _clip("chain_name", chain_name),
        "city": _clip("city", loc.city),
        "postcode": (loc.postcode or None) and loc.postcode[:20],
        "country_code": (loc.country_code or "").upper()[:2],
        "osm_type": loc.osm_type,
        "osm_id": loc.osm_id,
        "lat": loc.lat,
        "lon": loc.lon,
    }


def sync_open_prices(
    client: OpenPricesClient,
    now: datetime,
    *,
    off_lookup=enrichment.off.lookup,
    allow_deletions: bool = False,
    stopping: Callable[[], bool] | None = None,
) -> IngestionRun:
    """Pull the window and write it. ``stopping`` (the worker's stop request) is checked
    between offers in the write phase: a shutdown ends the run as "interrupted" with nothing
    deleted, and ``due`` retries it on the next start."""
    run = IngestionRun.objects.create(connector=CONNECTOR, category_slug="all", started_at=now)
    since = (now - settings.OBSERVED_MAX_AGE).date()
    try:
        fetched = client.fetch_window(since, settings.SITE_CURRENCY)
    except ConnectorError as exc:
        return _fail(run, str(exc))
    except Stopping:  # the worker is shutting down; due() retries at once on the next start
        return _fail(run, INTERRUPTED)
    except BudgetExhausted:  # no token within the wait (or shutting down); the next due check retries
        return _fail(run, "budget exhausted")
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

    # Compare against stored rows this pull could still return: inside the window,
    # and (with assisted entries off) not assisted. Rows that aged out or were
    # switched off are expected to go, so they must not trip the guard.
    keepable = OpenPriceObservation.objects.filter(observed_on__gte=since)
    if not settings.OPEN_PRICES_INCLUDE_ASSISTED:
        keepable = keepable.exclude(source__contains="/experiments/")
    stored = keepable.count()
    protected = set(getattr(client, "unparsed_ids", ()))  # seen upstream, just unreadable
    safe_to_delete = allow_deletions or not (stored and len(kept) < stored * MIN_KEPT_RATIO)
    if not safe_to_delete:
        logger.warning(
            "open prices: suspiciously small window (kept %s of %s stored); deletions skipped",
            len(kept), stored,
        )
        run.error = "suspiciously small window; deletions skipped"

    try:
        touched, keep_offers, skipped = _write(
            run, kept, protected, safe_to_delete, now, off_lookup, skipped, stopping
        )
    except Stopping:  # offers written so far stay; nothing was deleted or merged
        return _fail(run, INTERRUPTED)
    except Exception as exc:  # the write transaction rolled back; prior state is intact
        return _fail(run, type(exc).__name__)

    run.skipped = skipped
    run.status = IngestionRun.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()
    return run


def upsert_observations(
    kept: list[Observation],
    now: datetime,
    *,
    off_lookup,
    run: IngestionRun | None,
    prune=None,
    stopping: Callable[[], bool] | None = None,
) -> tuple[set[int], set[int], int]:
    """Write stores, observations and the latest observed offer per (store, UPC).

    Offers that match no category are kept as generic observed offers. Returns
    (touched product ids, pks of the observed offers written or already present,
    skipped count). Nothing is deleted and no product is merged here; ``prune``,
    if given, runs inside the transaction that wrote the observations.
    """
    skipped = 0
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
                    "categories": list(obs.categories),
                    "price": obs.price,
                    "currency": obs.currency,
                    "is_sale": obs.is_sale,
                    "observed_on": obs.date,
                    "proof_type": obs.proof_type[:20] if obs.proof_type else None,
                    "source": obs.source[:200] if obs.source else None,
                    "fetched_at": now,
                },
            )
        if prune is not None:
            prune()

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
    keep_offers: set[int] = set()  # pks of observed offers that must survive

    for (store_code, upc), obs in latest.items():
        if stopping is not None and stopping():
            raise Stopping()
        raw = to_raw_offer(obs, now)
        existing = _offer_pk(raw.retailer, raw.retailer_item_id)
        if existing is not None:
            keep_offers.add(existing)  # an error below must not delete the existing offer
        try:
            off = observed_off_product(off_lookup(upc), obs.labels, obs.quantity, obs.categories)
            normalized = normalize(raw, categories, store_brands, off=off)
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
                    off=off,
                    store_brands=store_brands,
                )
        except Exception as exc:  # one bad offer must not poison the run
            skipped += 1
            if run is not None and not run.error:
                run.error = f"{type(exc).__name__} on {raw.retailer}:{raw.retailer_item_id}"[:500]
            continue
        keep_offers.add(_offer_pk(raw.retailer, raw.retailer_item_id))
        if run is not None and normalized.category is not None:
            run.matched += 1
            if normalized.unresolved:
                run.unresolved += 1
            else:
                run.fully_parsed += 1
    return touched, keep_offers, skipped


def _offer_pk(retailer, item_id):
    return (
        Offer.objects.filter(retailer=retailer, retailer_item_id=item_id)
        .values_list("pk", flat=True)
        .first()
    )


def _write(run, kept, protected, safe_to_delete, now, off_lookup, skipped, stopping=None):
    def prune():
        keep_ids = [o.price_id for o in kept] + list(protected)
        OpenPriceObservation.objects.exclude(price_id__in=keep_ids).delete()
        Store.objects.filter(observations__isnull=True).delete()

    touched, keep_offers, more_skipped = upsert_observations(
        kept, now, off_lookup=off_lookup, run=run, prune=prune if safe_to_delete else None,
        stopping=stopping,
    )
    skipped += more_skipped

    # Offers behind observations that were seen upstream but unreadable stay.
    for kept_obs in OpenPriceObservation.objects.filter(price_id__in=protected).select_related("store"):
        pk = _offer_pk(kept_obs.store.chain_code, f"{kept_obs.upc}@{kept_obs.store.code}")
        if pk is not None:
            keep_offers.add(pk)

    if safe_to_delete:
        with transaction.atomic():
            doomed = Offer.objects.filter(kind=Offer.Kind.OBSERVED).exclude(pk__in=keep_offers)
            touched.update(doomed.values_list("product_id", flat=True))
            doomed.delete()

    for pk in sorted(touched):
        try:
            product = Product.objects.filter(pk=pk).first()
            if product is not None:
                merge_product(product, now)
        except Exception as exc:
            skipped += 1
            if not run.error:
                run.error = f"{type(exc).__name__} merging product {pk}"[:500]
    return touched, keep_offers, skipped
