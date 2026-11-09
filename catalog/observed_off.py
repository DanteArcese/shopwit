"""Open Food Facts product data for observed offers' barcodes (#88).

Open Prices only carries its own cached copy of a product, which often has no size.
After an Open Prices sync, barcodes of observed offers that have no ``OffEntry`` (or
one without a quantity, last checked more than ``RECHECK_AFTER`` ago) are looked up
one by one in the OFF product API, drawing from the shared "off-product" budget
(1 request per second, a daily cap). Each answer is stored as an ``OffEntry`` and the
offers on those barcodes are normalized again, so quantity, labels and categories come
from OFF's structured fields. Curated-category offers go first.

Failure-safe: a 429, 5xx or transport error stores nothing and the barcode is asked
again on a later run (never in the same run); a 404 or another non-retryable answer
is recorded like "not found", so a bad barcode does not eat the daily cap. An
exhausted budget or a shutdown ends the run early. The next Open Prices sync
normalizes every observed offer again anyway, with whatever ``OffEntry`` rows exist.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import enrichment.off
from connectors.live import BudgetExhausted, Stopping, retry_after
from connectors.open_prices import PRICE_PAGE
from enrichment.off import FIELDS, Source
from normalize import load_categories, load_store_brands, normalize
from normalize.generic import off_category_path
from normalize.types import RawOffer

from .ingest import INTERRUPTED, _upsert
from .merge import merge_product
from .nutrition import refresh_nutrition
from .observed import observed_off_product
from .models import IngestionRun, OffEntry, Offer, OpenPriceObservation, Product

logger = logging.getLogger(__name__)

CONNECTOR = "off-product"  # IngestionRun.connector and the SourceBudget key
DEFAULT_LIMIT = 300  # barcodes per run; the budget's daily cap also applies
RECHECK_AFTER = timedelta(
    days=30
)  # an entry without a quantity is asked again after this
CHUNK = 1000


def run_limit(now: datetime) -> int:
    """Barcodes per run: the boosted daily cap during the first-week boost, else 300."""
    until = settings.OFF_PRODUCT_BOOST_UNTIL
    if until is not None and now.astimezone(UTC).date() <= until:
        return settings.OFF_PRODUCT_BOOST_DAILY
    return DEFAULT_LIMIT


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
    rejected: int = 0  # non-retryable answer without a product (400, 403, unreadable)
    failed: int = 0
    nutrition: int = 0
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
            "rejected",
            "failed",
            "nutrition",
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


def nutrition_candidates(limit: int, exclude: set[str]) -> list[tuple[str, Source]]:
    """OFF entries whose nutrition was never checked (no 404 placeholders), in barcode order.

    The pk is the UPC string, so the order is lexicographic: stable across runs, not by age."""
    if limit <= 0:
        return []
    rows = (
        OffEntry.objects.filter(
            nutrition_checked_at__isnull=True, source="off", last_modified__isnull=False
        )
        .exclude(pk__in=exclude)
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    return [(upc, "off") for upc in rows]


def _lookup(client: httpx.Client, budget, source: Source, upc: str, stats: Stats):
    """One budgeted request, no retry: a record, "missing", "rejected" (a non-retryable
    answer without a product), or None (429, 5xx or transport error: ask again later)."""
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
    return enrichment.off.read_product(response, source, upc) or "rejected"


def _store(upc: str, source: Source, result, now: datetime, stats: Stats) -> None:
    if result in ("missing", "rejected"):
        if result == "missing":
            stats.not_found += 1
        else:
            stats.rejected += 1
        # nutrition_checked_at: "checked, nothing usable", so it is not fetched again
        if not OffEntry.objects.filter(pk=upc).update(
            fetched_at=now, nutrition_checked_at=now
        ):
            OffEntry.objects.get_or_create(
                upc=upc,
                defaults={
                    "source": source,
                    "labels": [],
                    "quantity": None,
                    "nutrition_checked_at": now,
                },
            )
        return  # an existing entry keeps its data; only the check time moves
    stats.stored += 1
    if enrichment.off._save(result):
        stats.changed.add(upc)
    else:  # the cached row is newer; keep it, but fill nutrition it never had, then mark checked
        n = result.nutrition
        if n is not None:
            OffEntry.objects.filter(
                pk=upc,
                nutrition_checked_at__isnull=True,
                nutrition_per__isnull=True,
                protein_100g__isnull=True,
                kcal_100g__isnull=True,
            ).update(nutrition_per=n.basis, protein_100g=n.protein, kcal_100g=n.kcal)
        OffEntry.objects.filter(pk=upc).update(fetched_at=now, nutrition_checked_at=now)
        stats.changed.add(upc)  # its offers' nutrition is recomputed from the cached row


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
    offers = []
    ordered = sorted(upcs)
    for start in range(0, len(ordered), CHUNK):
        prefix = Q()
        for upc in ordered[start : start + CHUNK]:
            prefix |= Q(retailer_item_id__startswith=f"{upc}@")
        offers.extend(
            Offer.objects.filter(prefix, kind=Offer.Kind.OBSERVED, store__isnull=False)
        )
    offers.sort(key=lambda o: o.pk)
    model_cache: dict = {}
    touched: set[int] = set()
    try:
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
                off = observed_off_product(
                    off_lookup(upc), row.labels, row.quantity, row.categories
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
    finally:  # what was written is merged even when stopping
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
    limit: int | None = None,
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
            limit = run_limit(now) if limit is None else max(0, limit)
            sizes = candidates(now, limit)
            nutrition = nutrition_candidates(limit - len(sizes), {u for u, _ in sizes})
            stats.nutrition = len(nutrition)
            wanted = sizes + nutrition
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
        finally:
            # Stored entries are marked checked, so their offers must be renormalized and
            # their nutrition refreshed even when the loop was stopped or failed. The original
            # exception (if any) still propagates after this.
            try:
                renormalize(stats.changed, now, stats, stopping=stopping)
            finally:
                refresh_nutrition(stats.changed)
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
