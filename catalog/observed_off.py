"""Open Food Facts product data for observed offers' barcodes (#88).

Open Prices only carries its own cached copy of a product, which often has no size.
After an Open Prices sync, barcodes of observed offers that have no ``OffEntry`` (or
one without a quantity, last checked more than ``RECHECK_AFTER`` ago) are looked up
one by one in the OFF product API, drawing from the shared "off-product" budget
(1 request per second, a daily cap). Each answer is stored as an ``OffEntry`` and the
offers on those barcodes are normalized again, so quantity, labels and categories come
from OFF's structured fields. Curated-category offers go first.

Failure-safe: a failed lookup stores nothing and is retried on a later run; an
exhausted budget or a shutdown ends the run early. The next Open Prices sync
normalizes every observed offer again anyway, with whatever ``OffEntry`` rows exist.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

import enrichment.off
from connectors.live import BudgetExhausted, Stopping, retry_after
from connectors.open_prices import PRICE_PAGE
from enrichment.off import FIELDS, Source
from normalize import load_categories, load_store_brands, normalize
from normalize.generic import off_category_path
from normalize.types import OffProduct, RawOffer

from .ingest import INTERRUPTED, _upsert
from .merge import merge_product
from .models import IngestionRun, OffEntry, Offer, OpenPriceObservation, Product

logger = logging.getLogger(__name__)

CONNECTOR = "off-product"  # IngestionRun.connector and the SourceBudget key
DEFAULT_LIMIT = 300  # barcodes per run; the budget's daily cap also applies
RECHECK_AFTER = timedelta(
    days=30
)  # an entry without a quantity is asked again after this
CHUNK = 1000


@dataclass
class _Barcode:
    upc: str
    curated: bool = False
    household: bool = False
    latest: object = None  # newest observed_on among its offers


@dataclass
class Stats:
    requests: int = 0
    stored: int = 0
    not_found: int = 0
    failed: int = 0
    renormalized: int = 0
    matched: int = 0
    fully_parsed: int = 0
    unresolved: int = 0
    errors: int = 0
    stopped: str = ""
    changed: set[str] = field(default_factory=set)

    def line(self) -> str:
        keys = (
            "requests",
            "stored",
            "not_found",
            "failed",
            "renormalized",
            "matched",
            "errors",
        )
        return " ".join(f"{k}={getattr(self, k)}" for k in keys)


def _upc(retailer_item_id: str) -> str:
    return retailer_item_id.split("@", 1)[0]


def candidates(now: datetime, limit: int) -> list[tuple[str, Source]]:
    """Barcodes of observed offers to look up, curated ones first, then the newest."""
    barcodes: dict[str, _Barcode] = {}
    rows = Offer.objects.filter(kind=Offer.Kind.OBSERVED).values_list(
        "retailer_item_id", "category__slug", "observed_on"
    )
    for item_id, slug, observed_on in rows.iterator():
        upc = _upc(item_id)
        entry = barcodes.setdefault(upc, _Barcode(upc))
        if slug:
            entry.curated = True
            entry.household = entry.household or slug.startswith("household/")
        if observed_on and (entry.latest is None or observed_on > entry.latest):
            entry.latest = observed_on

    upcs = list(barcodes)
    known: dict[str, tuple[str | None, datetime]] = {}
    for start in range(0, len(upcs), CHUNK):
        for pk, quantity, fetched_at in OffEntry.objects.filter(
            pk__in=upcs[start : start + CHUNK]
        ).values_list("pk", "quantity", "fetched_at"):
            known[pk] = (quantity, fetched_at)

    stale = now - RECHECK_AFTER
    wanted = []
    for upc, entry in barcodes.items():
        stored = known.get(upc)
        if stored is not None and ((stored[0] or "").strip() or stored[1] > stale):
            continue  # has a quantity, or was asked recently
        newest = entry.latest.toordinal() if entry.latest else 0
        wanted.append(((not entry.curated, stored is not None, -newest, upc), entry))
    wanted.sort(key=lambda pair: pair[0])
    return [(e.upc, "opf" if e.household else "off") for _, e in wanted[:limit]]


def _lookup(client: httpx.Client, budget, source: Source, upc: str, stats: Stats):
    """One budgeted request, no retry: a record, "missing", or None (failed)."""
    budget.wait(CONNECTOR)
    stats.requests += 1
    try:
        response = client.get(
            enrichment.off.product_url(source, upc), params={"fields": FIELDS}
        )
    except httpx.TransportError:
        response = None
    budget.report(
        CONNECTOR,
        response.status_code if response is not None else None,
        retry_after(response) if response is not None else None,
    )
    if enrichment.off.retryable(response):
        return None
    return enrichment.off.read_product(response, source, upc)


def _store(upc: str, source: Source, result, now: datetime, stats: Stats) -> None:
    if result == "missing":
        stats.not_found += 1
        if not OffEntry.objects.filter(pk=upc).update(fetched_at=now):
            OffEntry.objects.get_or_create(
                upc=upc, defaults={"source": source, "labels": [], "quantity": None}
            )
        return  # an existing entry keeps its data; only the check time moves
    stats.stored += 1
    if enrichment.off._save(result):
        stats.changed.add(upc)
    else:  # the cached row is newer; remember that it was checked
        OffEntry.objects.filter(pk=upc).update(fetched_at=now)


def _raw(offer: Offer, row: OpenPriceObservation, now: datetime) -> RawOffer:
    """The offer as the Open Prices sync built it (see connectors.open_prices.to_raw_offer)."""
    path = off_category_path(row.categories)
    return RawOffer(
        retailer=offer.retailer,
        retailer_item_id=offer.retailer_item_id,
        title=row.product_name,
        price=row.price,
        currency=row.currency,
        url=PRICE_PAGE.format(id=row.price_id),
        fetched_at=now,
        upc=row.upc,
        brand=row.brands,
        size_text=row.quantity,
        fields={"category_path": path} if path else {},
    )


def renormalize(
    upcs: set[str],
    now: datetime,
    stats: Stats,
    *,
    off_lookup=enrichment.off.lookup,
    stopping: Callable[[], bool] | None = None,
) -> None:
    """Normalize the observed offers on ``upcs`` again from their latest observation."""
    if not upcs:
        return
    categories = load_categories(settings.CATEGORIES_DIR)
    store_brands = load_store_brands(settings.STORE_BRANDS_FILE)
    offers = [
        o
        for o in Offer.objects.filter(
            kind=Offer.Kind.OBSERVED, store__isnull=False
        ).order_by("pk")
        if _upc(o.retailer_item_id) in upcs
    ]
    model_cache: dict = {}
    touched: set[int] = set()
    for offer in offers:
        if stopping is not None and stopping():
            raise Stopping()
        upc = _upc(offer.retailer_item_id)
        row = (
            OpenPriceObservation.objects.filter(store_id=offer.store_id, upc=upc)
            .order_by("-observed_on", "-price_id")
            .first()
        )
        if row is None:
            continue
        try:
            raw = _raw(offer, row, now)
            off = off_lookup(upc) or OffProduct(
                labels=frozenset(row.labels),
                quantity=row.quantity,
                categories=tuple(row.categories),
            )
            normalized = normalize(raw, categories, store_brands, off=off)
            with transaction.atomic():
                _upsert(
                    raw,
                    normalized,
                    model_cache,
                    now,
                    touched,
                    records_price_history=False,
                    extra_defaults={
                        "kind": Offer.Kind.OBSERVED,
                        "observed_on": row.observed_on,
                        "is_sale": row.is_sale,
                        "store_id": offer.store_id,
                        "last_seen_at": offer.last_seen_at,  # nothing new was seen
                    },
                    off=off,
                    store_brands=store_brands,
                )
        except Exception as exc:  # one bad offer must not stop the rest
            stats.errors += 1
            logger.warning(
                "off sizes: renormalizing offer %s failed: %s",
                offer.pk,
                type(exc).__name__,
            )
            continue
        stats.renormalized += 1
        if normalized.category is not None:
            stats.matched += 1
            if normalized.unresolved:
                stats.unresolved += 1
            else:
                stats.fully_parsed += 1
    for pk in sorted(touched):
        try:
            product = Product.objects.filter(pk=pk).first()
            if product is not None:
                merge_product(product, now)
        except Exception as exc:
            stats.errors += 1
            logger.warning(
                "off sizes: merging product %s failed: %s", pk, type(exc).__name__
            )


def backfill_sizes(
    now: datetime,
    budget,
    *,
    limit: int = DEFAULT_LIMIT,
    client: httpx.Client | None = None,
    stopping: Callable[[], bool] | None = None,
) -> tuple[IngestionRun, Stats]:
    """Look up OFF data for observed barcodes and normalize their offers again.

    ``budget`` is a ``DbBudget`` (or anything with its ``wait`` and ``report``).
    Always returns a finished run row; never raises.
    """
    run = IngestionRun.objects.create(
        connector=CONNECTOR, category_slug="all", started_at=now
    )
    stats = Stats()
    owned = client is None
    try:
        if owned:
            client = enrichment.off.default_client()
        try:
            wanted = candidates(now, max(0, limit))
            run.fetched = len(wanted)
            for upc, source in wanted:
                if stopping is not None and stopping():
                    raise Stopping()
                result = _lookup(client, budget, source, upc, stats)
                if result is None:
                    stats.failed += 1
                    continue
                _store(upc, source, result, now, stats)
        except Stopping:
            raise
        except BudgetExhausted:
            stats.stopped = "budget exhausted"
        renormalize(stats.changed, now, stats, stopping=stopping)
    except Stopping:
        stats.stopped = INTERRUPTED
    except Exception as exc:  # never leave a RUNNING row; type name only
        stats.stopped = type(exc).__name__
        logger.error("off sizes failed: %s", type(exc).__name__)
    finally:
        if owned and client is not None:
            client.close()

    run.matched = stats.matched
    run.fully_parsed = stats.fully_parsed
    run.unresolved = stats.unresolved
    run.skipped = stats.failed + stats.errors
    failed = stats.stopped not in ("", "budget exhausted")
    run.status = IngestionRun.Status.FAILED if failed else IngestionRun.Status.SUCCEEDED
    run.error = stats.stopped[:500]
    run.finished_at = timezone.now()
    run.save()
    return run, stats
