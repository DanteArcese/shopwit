"""Scheduler: refresh each (connector, active category) pair once per REFRESH_INTERVAL."""

from __future__ import annotations

import logging
import signal
import threading
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from connectors import registry
from connectors.base import Connector

from connectors.open_prices import OpenPricesClient

from . import observed
from .ingest import ingest
from .models import Category, IngestionRun

logger = logging.getLogger(__name__)


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


def tick(now: datetime, connectors: Mapping[str, Connector] | None = None) -> list[IngestionRun]:
    if connectors is None:
        connectors = registry.available()
        try:
            return tick(now, connectors)
        finally:
            close_connectors(connectors)
    runs = []
    for name, slug in due(now, connectors):
        try:
            run = ingest(connectors[name], Category.objects.get(slug=slug), now)
        except Exception as exc:  # one bad pair must not stop the others
            logger.error("ingest connector=%s category=%s failed: %s", name, slug, type(exc).__name__)
            runs.append(_fail_run(name, slug, now, exc))
            continue
        log_run(run)
        runs.append(run)
    if settings.OPEN_PRICES_ENABLED:
        try:
            open_prices_due = observed.due(now)
        except Exception as exc:  # a DB error here must not stop the loop
            logger.error("open prices due check failed: %s", type(exc).__name__)
            open_prices_due = False
        if open_prices_due:
            runs.extend(_open_prices(now))
    return runs


def _open_prices(now: datetime) -> list[IngestionRun]:
    client = None
    try:
        client = OpenPricesClient()
        run = observed.sync_open_prices(client, now)
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


def run_loop(
    poll_seconds: float = 300,
    *,
    once: bool = False,
    wait: Callable[[float], object] | None = None,
    now: Callable[[], datetime] = timezone.now,
    stop: threading.Event | None = None,
    connectors: Mapping[str, Connector] | None = None,
) -> None:
    """Tick, then wait; a stop request ends the wait at once and the loop exits."""
    stop = stop or threading.Event()
    wait = wait or stop.wait
    owned = connectors is None
    if owned:
        connectors = registry.available()  # built once, so HTTP clients are reused, not leaked
    try:
        while not stop.is_set():
            tick(now(), connectors)
            if once:
                return
            wait(poll_seconds)
    finally:
        if owned:
            close_connectors(connectors)


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
