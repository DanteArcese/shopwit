"""Scheduler: refresh each (connector, active category) pair once per REFRESH_INTERVAL."""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable, Mapping
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from connectors import registry
from connectors.base import Connector

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
        "fully_parsed=%s unresolved=%s skipped=%s",
        run.connector, run.category_slug, run.status, run.fetched, run.matched,
        run.fully_parsed, run.unresolved, run.skipped,
    )


def tick(now: datetime, connectors: Mapping[str, Connector] | None = None) -> list[IngestionRun]:
    if connectors is None:
        connectors = registry.available()
    runs = []
    for name, slug in due(now, connectors):
        run = ingest(connectors[name], Category.objects.get(slug=slug), now)
        log_run(run)
        runs.append(run)
    return runs


def run_loop(
    poll_seconds: float = 300,
    *,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = timezone.now,
    should_stop: Callable[[], bool] = lambda: False,
    connectors: Mapping[str, Connector] | None = None,
) -> None:
    """Tick, then sleep; a stop request is honoured after the current tick finishes."""
    while True:
        tick(now(), connectors)
        if once or should_stop():
            return
        sleep(poll_seconds)
        if should_stop():
            return


def install_sigterm_flag() -> Callable[[], bool]:
    stopped = {"flag": False}

    def handler(signum, frame):
        stopped["flag"] = True

    signal.signal(signal.SIGTERM, handler)
    return lambda: stopped["flag"]
