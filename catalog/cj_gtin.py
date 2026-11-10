"""CJ Affiliate barcode lookups for products we already show (#111).

Once a day, after the Open Prices sync, the barcodes of fresh observed offers are looked
up at CJ one by one (curated categories first, then newest), drawing from the shared "cj"
budget. Whatever CJ returns (joined advertisers only, new and in stock; see
``connectors.cj``) is written through ``ingest_live`` exactly like a live search result, so
it lands as ``cj-*`` offers on the existing product (matched by UPC). Every barcode that
got an answer, even an empty one, is remembered in ``CjGtinCheck``. A barcode that found
offers is due again after ``FOUND_RECHECK_AFTER`` (before its 24 h offers go stale); one
that found nothing waits ``EMPTY_RECHECK_AFTER``.

The run is skipped (one request) while no advertiser has joined us, and is bounded by
``DEFAULT_LIMIT`` barcodes and ``CJ_GTIN_MAX_SECONDS`` of wall time.

Failure-safe: an input-specific error (HTTP 400, GraphQL ``errors``) marks that barcode
checked with nothing found and moves on; any other ``ConnectorError`` (transport, 429,
5xx, ...) stops the run and the failed barcode is not marked checked; an exhausted
budget, a shutdown or the time cap ends the run cleanly. Only exception type names are
logged or stored, never messages (they could carry credentials).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from connectors.base import ConnectorError
from connectors.live import BudgetExhausted, LiveSearchResult, Stopping

from .freshness import fresh_observed
from .ingest import INTERRUPTED, ingest_live
from .models import CjGtinCheck, IngestionRun

logger = logging.getLogger(__name__)

CONNECTOR = "cj-gtin"  # IngestionRun.connector
BUDGET_KEY = "cj"
DEFAULT_LIMIT = 200
FOUND_RECHECK_AFTER = timedelta(hours=20)  # under CjConnector.max_cache_age (24 h)
EMPTY_RECHECK_AFTER = timedelta(days=7)
CJ_GTIN_MAX_SECONDS = 600  # wall-clock cap: the run blocks the scheduler tick
BUDGET_EXHAUSTED = "budget exhausted"
TIME_CAP = "time cap"
NO_JOINED = "no joined advertisers"
CLEAN_STOPS = ("", BUDGET_EXHAUSTED, TIME_CAP, NO_JOINED)


@dataclass
class Stats:
    looked_up: int = 0  # barcodes that got an answer
    found: int = 0  # of those, barcodes with at least one offer
    offers: int = 0
    errors: int = 0
    stopped: str = ""

    def line(self) -> str:
        return " ".join(
            f"{k}={getattr(self, k)}" for k in ("looked_up", "found", "offers", "errors")
        )


class _WaitingBudget:
    """Adapts a ``DbBudget`` to the connector's ``acquire``/``report``: ``acquire`` waits for a
    token (``BudgetExhausted`` only once the wait would be long), as the OFF backfill does."""

    def __init__(self, budget) -> None:
        self._budget = budget

    def acquire(self, key: str) -> None:
        self._budget.wait(key)

    def report(self, key: str, status: int | None, retry_after: float | None = None) -> None:
        self._budget.report(key, status, retry_after)


def candidates(now: datetime, limit: int) -> list[str]:
    """Barcodes of fresh observed offers that are due (found: ``FOUND_RECHECK_AFTER``;
    empty: ``EMPTY_RECHECK_AFTER``): curated categories first, then newest ``observed_on``,
    then barcode order."""
    rows = fresh_observed(now).values_list("retailer_item_id", "category_id", "observed_on")
    seen: dict[str, list] = {}  # upc -> [curated, newest observed_on]
    for item_id, category_id, observed_on in rows.iterator():
        upc = item_id.split("@", 1)[0]
        entry = seen.setdefault(upc, [False, None])
        entry[0] = entry[0] or category_id is not None
        if observed_on and (entry[1] is None or observed_on > entry[1]):
            entry[1] = observed_on
    recent = set(
        CjGtinCheck.objects.filter(
            Q(found__gt=0, checked_at__gt=now - FOUND_RECHECK_AFTER)
            | Q(found__lte=0, checked_at__gt=now - EMPTY_RECHECK_AFTER)
        ).values_list("pk", flat=True)
    )
    wanted = [
        ((not curated, -(latest.toordinal() if latest else 0), upc), upc)
        for upc, (curated, latest) in seen.items()
        if upc not in recent
    ]
    wanted.sort()
    return [upc for _, upc in wanted[: max(0, limit)]]


def backfill(
    connector,
    budget,
    now: datetime,
    limit: int,
    stopping: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    max_seconds: float = CJ_GTIN_MAX_SECONDS,
) -> Stats:
    """Look up each candidate barcode at CJ and write the offers found.

    ``budget`` is a ``DbBudget`` (or anything with ``wait`` and ``report``). Expected
    stops (connector error, budget, shutdown, time cap) are recorded in
    ``Stats.stopped``; anything unexpected propagates. ``clock`` (seconds) is checked
    before each barcode; once ``max_seconds`` have passed the run ends with "time cap".
    """
    stats = Stats()
    waiting = _WaitingBudget(budget)
    started = clock()
    try:
        for upc in candidates(now, limit):
            if stopping is not None and stopping():
                raise Stopping()
            if clock() - started >= max_seconds:
                stats.stopped = TIME_CAP
                break
            try:
                result = connector.lookup_gtin(upc, waiting)
            except ConnectorError as exc:
                stats.errors += 1
                logger.warning("cj gtin: lookup failed: %s", type(exc).__name__)
                if getattr(exc, "retryable", True) is False:
                    # Input-specific (HTTP 400, GraphQL errors): asking again fails again,
                    # so mark it like an empty answer and move on.
                    CjGtinCheck.objects.update_or_create(
                        upc=upc, defaults={"checked_at": now, "found": 0}
                    )
                    continue
                stats.stopped = type(exc).__name__  # not marked: asked again on a later run
                break
            if result.offers:
                ingest_live(LiveSearchResult(offers=list(result.offers)), "cj", now)
            CjGtinCheck.objects.update_or_create(
                upc=upc, defaults={"checked_at": now, "found": len(result.offers)}
            )
            stats.looked_up += 1
            stats.found += 1 if result.offers else 0
            stats.offers += len(result.offers)
    except Stopping:
        stats.stopped = INTERRUPTED
    except BudgetExhausted:
        stats.stopped = BUDGET_EXHAUSTED
    return stats


def run_backfill(
    connector,
    budget,
    now: datetime,
    limit: int = DEFAULT_LIMIT,
    stopping: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[IngestionRun, Stats]:
    """``backfill`` with an ``IngestionRun`` row (connector "cj-gtin"); never raises.

    First asks CJ (one budgeted request) whether any advertiser has joined us; when none
    has, every lookup would come back empty, so the run is recorded as succeeded with the
    note "no joined advertisers" and no barcode is looked up.
    """
    run = IngestionRun.objects.create(connector=CONNECTOR, category_slug="all", started_at=now)
    stats = Stats()
    try:
        if not connector.has_joined_advertisers(_WaitingBudget(budget)):
            stats.stopped = NO_JOINED
        else:
            stats = backfill(connector, budget, now, limit, stopping, clock)
    except Stopping:
        stats.stopped = INTERRUPTED
    except BudgetExhausted:
        stats.stopped = BUDGET_EXHAUSTED
    except Exception as exc:  # never leave a RUNNING row; type name only
        stats.stopped = type(exc).__name__
        logger.error("cj gtin failed: %s", type(exc).__name__)
    run.fetched = stats.looked_up
    run.matched = stats.found
    run.skipped = stats.errors
    failed = stats.stopped not in CLEAN_STOPS
    run.status = IngestionRun.Status.FAILED if failed else IngestionRun.Status.SUCCEEDED
    run.error = stats.stopped[:500]
    run.finished_at = timezone.now()
    run.save()
    return run, stats
