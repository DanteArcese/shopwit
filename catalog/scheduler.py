"""Scheduler: refresh each (connector, active category) pair once per REFRESH_INTERVAL."""

from __future__ import annotations

import logging
import signal
import threading
import time
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from connectors import registry
from connectors.registry import live_source_info
from connectors.base import Connector

from connectors.open_prices import OpenPricesClient

from . import observed, observed_off
from .budget import DbBudget, seed_budgets
from .demand import prune_demand, warm_due, warm_round
from .fetch import fetch_loop, housekeeping
from .ingest import ingest
from .models import Category, IngestionRun

logger = logging.getLogger(__name__)

SESSION_CLEANUP_INTERVAL = timedelta(days=1)
# Per-process marker: a restarted worker clears once more, which is harmless.
_sessions_cleared_at: datetime | None = None
_feedback_purged_at: datetime | None = None
_magic_purged_at: datetime | None = None
_demand_pruned_at: datetime | None = None
DEMAND_PRUNE_INTERVAL = timedelta(days=1)


def due(now: datetime, connectors: Mapping[str, Connector]) -> list[tuple[str, str]]:
    cutoff = now - settings.REFRESH_INTERVAL
    pairs = []
    for slug in Category.objects.filter(active=True).order_by("slug").values_list("slug", flat=True):
        for name in connectors:
            fresh = IngestionRun.objects.filter(
                connector=name,
                category_slug=slug,
                status=IngestionRun.Status.SUCCEEDED,
                started_at__gte=cutoff,
            ).exists()
            if not fresh:
                pairs.append((name, slug))
    return pairs


def log_run(run: IngestionRun) -> None:
    logger.info(
        "ingest connector=%s category=%s status=%s fetched=%s matched=%s "
        "fully_parsed=%s unresolved=%s skipped=%s error=%s",
        run.connector, run.category_slug, run.status, run.fetched, run.matched,
        run.fully_parsed, run.unresolved, run.skipped, run.error,
    )


def _fail_run(name: str, slug: str, now: datetime, exc: Exception) -> IngestionRun:
    """Mark the run an unexpected error left behind as failed (type name only, no message)."""
    run = (
        IngestionRun.objects.filter(
            connector=name, category_slug=slug, status=IngestionRun.Status.RUNNING
        )
        .order_by("-id")
        .first()
    ) or IngestionRun(connector=name, category_slug=slug, started_at=now)
    run.status = IngestionRun.Status.FAILED
    run.error = type(exc).__name__
    run.finished_at = timezone.now()
    run.save()
    return run


def close_connectors(connectors: Mapping[str, Connector]) -> None:
    for connector in connectors.values():
        close = getattr(connector, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                logger.warning("closing connector %s failed: %s", connector.name, type(exc).__name__)


def tick(
    now: datetime,
    connectors: Mapping[str, Connector] | None = None,
    *,
    budget: DbBudget | None = None,
) -> list[IngestionRun]:
    """One pass. With ``budget``, scheduled requests draw from the shared SourceBudget rows."""
    if connectors is None:
        connectors = registry.available()
        try:
            return tick(now, connectors, budget=budget)
        finally:
            close_connectors(connectors)
    runs = []
    for name, slug in due(now, connectors):
        if budget is not None and budget.stopping:
            return runs  # shutting down: leave the remaining pairs for the next start
        try:
            run = ingest(connectors[name], Category.objects.get(slug=slug), now, budget=budget)
        except Exception as exc:  # one bad pair must not stop the others
            logger.error("ingest connector=%s category=%s failed: %s", name, slug, type(exc).__name__)
            runs.append(_fail_run(name, slug, now, exc))
            continue
        log_run(run)
        runs.append(run)
    if budget is not None and budget.stopping:
        return runs
    if settings.OPEN_PRICES_ENABLED:
        try:
            open_prices_due = observed.due(now)
        except Exception as exc:  # a DB error here must not stop the loop
            logger.error("open prices due check failed: %s", type(exc).__name__)
            open_prices_due = False
        if open_prices_due:
            synced = _open_prices(now, budget)
            runs.extend(synced)
            # Then OFF sizes for the observed barcodes; it needs the shared budget.
            if (
                budget is not None
                and not budget.stopping
                and synced[-1].status == IngestionRun.Status.SUCCEEDED
            ):
                runs.append(_off_sizes(now, budget))
    fetch_housekeeping(now)
    demand_housekeeping(now)
    clear_expired_sessions(now)
    purge_feedback_emails(now)
    purge_magic_tokens(now)
    return runs


def clear_expired_sessions(now: datetime) -> bool:
    """Run Django's clearsessions at most once per SESSION_CLEANUP_INTERVAL.

    Expired sessions (and the settings in them, such as a saved postal code)
    are deleted. Never raises; a failure waits for the next interval.
    """
    global _sessions_cleared_at
    if _sessions_cleared_at is not None and now - _sessions_cleared_at < SESSION_CLEANUP_INTERVAL:
        return False
    _sessions_cleared_at = now
    try:
        call_command("clearsessions")
    except Exception as exc:
        logger.error("clearsessions failed: %s", type(exc).__name__)
        return False
    logger.info("clearsessions done")
    return True


def purge_feedback_emails(now: datetime) -> bool:
    """Blank stale feedback reply emails at most once per SESSION_CLEANUP_INTERVAL.

    Never raises; a failure waits for the next interval.
    """
    global _feedback_purged_at
    if _feedback_purged_at is not None and now - _feedback_purged_at < SESSION_CLEANUP_INTERVAL:
        return False
    _feedback_purged_at = now
    try:
        from feedback.cleanup import purge_reply_emails

        purged = purge_reply_emails(now)
    except Exception as exc:
        logger.error("feedback email purge failed: %s", type(exc).__name__)
        return False
    logger.info("feedback email purge done purged=%s", purged)
    return True


def purge_magic_tokens(now: datetime) -> bool:
    """Once per SESSION_CLEANUP_INTERVAL, delete sign-in link rows a day past expiry
    and expired cache rows (rate-limit counters).

    Never raises; a failure waits for the next interval.
    """
    global _magic_purged_at
    if _magic_purged_at is not None and now - _magic_purged_at < SESSION_CLEANUP_INTERVAL:
        return False
    _magic_purged_at = now
    try:
        from accounts import magic

        purged = magic.purge_expired(now)
        _purge_expired_cache_rows()
    except Exception as exc:
        logger.error("sign-in link purge failed: %s", type(exc).__name__)
        return False
    logger.info("sign-in link purge done purged=%s", purged)
    return True


def _purge_expired_cache_rows() -> None:
    """DatabaseCache only deletes expired rows when culling; delete them here."""
    from django.db import connection

    conf = settings.CACHES["default"]
    if conf["BACKEND"] != "django.core.cache.backends.db.DatabaseCache":
        return
    table = connection.ops.quote_name(conf["LOCATION"])
    with connection.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE expires < %s", [timezone.now()])


def demand_housekeeping(now: datetime) -> None:
    """Prune stale demand rows once a day and, every WARM_INTERVAL, queue warm refreshes.

    Never raises.
    """
    global _demand_pruned_at
    if _demand_pruned_at is None or now - _demand_pruned_at >= DEMAND_PRUNE_INTERVAL:
        _demand_pruned_at = now
        try:
            pruned = prune_demand(now.date())
            if pruned:
                logger.info("demand pruned=%s", pruned)
        except Exception as exc:
            logger.error("demand prune failed: %s", type(exc).__name__)
    try:
        sources = live_source_info(live_off=settings.LIVE_OFF_ENABLED)
        if sources and warm_due(now):  # no live source: nothing to warm
            logger.info("warm round jobs=%s", warm_round(now, sources))
    except Exception as exc:
        logger.error("warm round failed: %s", type(exc).__name__)


def fetch_housekeeping(now: datetime) -> dict[str, int] | None:
    """Expire, reclaim and prune fetch jobs. Never raises."""
    try:
        counts = housekeeping(now)
    except Exception as exc:
        logger.error("fetch housekeeping failed: %s", type(exc).__name__)
        return None
    if any(counts.values()):
        logger.info(
            "fetch housekeeping expired=%s requeued=%s stuck=%s deleted=%s",
            counts["expired"], counts["requeued"], counts["stuck"], counts["deleted"],
        )
    return counts


def _open_prices(now: datetime, budget: DbBudget | None = None) -> list[IngestionRun]:
    client = None
    try:
        client = (
            OpenPricesClient(budget_wait=budget.wait, budget_report=budget.report)
            if budget
            else OpenPricesClient()
        )
        run = observed.sync_open_prices(
            client, now, stopping=(lambda: budget.stopping) if budget else None
        )
    except Exception as exc:
        logger.error("ingest connector=%s failed: %s", observed.CONNECTOR, type(exc).__name__)
        return [_fail_run(observed.CONNECTOR, "all", now, exc)]
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("closing Open Prices client failed: %s", type(exc).__name__)
    log_run(run)
    return [run]


def _off_sizes(now: datetime, budget: DbBudget) -> IngestionRun:
    try:
        run, _ = observed_off.backfill_sizes(now, budget, stopping=lambda: budget.stopping)
    except Exception as exc:  # backfill_sizes never raises; this guards the unexpected
        logger.error("ingest connector=%s failed: %s", observed_off.CONNECTOR, type(exc).__name__)
        return _fail_run(observed_off.CONNECTOR, "all", now, exc)
    log_run(run)
    return run


def run_loop(
    poll_seconds: float = 300,
    *,
    once: bool = False,
    wait: Callable[[float], object] | None = None,
    now: Callable[[], datetime] = timezone.now,
    stop: threading.Event | None = None,
    connectors: Mapping[str, Connector] | None = None,
    fetch: bool = True,
) -> None:
    """Tick, then wait; a stop request ends the wait at once and the loop exits.

    With ``fetch`` (and not ``once``), ``settings.FETCH_THREADS`` daemon threads run
    the fetch loop alongside, sharing ``stop``; they are stopped and joined on exit.
    """
    stop = stop or StopEvent()
    wait = wait or stop.wait
    try:
        seed_budgets()
    except Exception as exc:  # budgeted calls fail (and retry) until the rows exist
        logger.error("seeding source budgets failed: %s", type(exc).__name__)
    budget = DbBudget(stop=stop)  # a stop request also ends any budget wait
    owned = connectors is None
    if owned:
        connectors = registry.available()  # built once, so HTTP clients are reused, not leaked
    threads: list[threading.Thread] = []
    try:
        if fetch and not once:
            if live_source_info(live_off=settings.LIVE_OFF_ENABLED):
                threads = start_fetch_threads(stop, budget)
            else:  # nothing to fetch from: idle threads would only poll the queue
                logger.info("no live search sources; fetch threads not started")
        while not stop.is_set():
            tick(now(), connectors, budget=budget)
            if once:
                return
            wait(poll_seconds)
    finally:
        if threads:
            stop.set()  # a no-op after SIGTERM: the join deadline keeps the signal's time
            stop_fetch_threads(threads, stop)
        if owned:
            close_connectors(connectors)


# Counted from the stop request (SIGTERM), so main-loop wind-down plus this join stays
# well inside compose's 60 s stop_grace_period.
FETCH_JOIN_TIMEOUT = 20.0


class StopEvent(threading.Event):
    """An Event that remembers when it was first set (monotonic seconds)."""

    def __init__(self) -> None:
        super().__init__()
        self.set_at: float | None = None

    def set(self) -> None:
        if self.set_at is None:
            self.set_at = time.monotonic()
        super().set()


def fetch_thread(stop: threading.Event, budget: DbBudget) -> None:
    """One fetch thread: builds its own live sources (no client is shared between threads),
    runs the fetch loop and closes them on exit."""
    sources: Mapping[str, object] = {}
    try:
        sources = registry.live_sources(live_off=settings.LIVE_OFF_ENABLED)
        logger.info(
            "fetch thread %s sources=%s",
            threading.current_thread().name,
            ",".join(sorted(sources)) or "none",
        )
        fetch_loop(stop, sources=sources, budget=budget)
    except Exception as exc:  # fetch_loop never raises; this guards the source factory
        logger.error("fetch thread failed: %s", type(exc).__name__)
    finally:
        close_connectors(sources)


def start_fetch_threads(stop: threading.Event, budget: DbBudget) -> list[threading.Thread]:
    threads = []
    for index in range(settings.FETCH_THREADS):
        thread = threading.Thread(
            target=fetch_thread,
            args=(stop, budget),
            name=f"fetch-{index}",
            daemon=True,  # never keeps the process alive past the join timeout
        )
        thread.start()
        threads.append(thread)
    return threads


def stop_fetch_threads(threads: list[threading.Thread], stop: threading.Event) -> None:
    """Join the fetch threads by FETCH_JOIN_TIMEOUT after the stop request (one deadline)."""
    set_at = getattr(stop, "set_at", None)
    deadline = (set_at if set_at is not None else time.monotonic()) + FETCH_JOIN_TIMEOUT
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
        if thread.is_alive():
            logger.warning("fetch thread %s did not stop in time", thread.name)


@contextmanager
def sigterm_stops(stop: threading.Event):
    """Set `stop` on SIGTERM for the duration; restores the previous handler."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
