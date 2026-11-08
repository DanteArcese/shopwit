"""Live fetch jobs: enqueue (web), claim and run (worker fetch threads), housekeeping.

Logs name the job id and source only, never the phrase.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from connectors.live import Budget, BudgetExhausted, LiveSearchSource, SourceInfo
from normalize.generic import is_personal

from .budget import DbBudget
from .ingest import ingest_live
from .models import FetchJob

# A child of the scheduler's logger, so run_scheduler's handler prints these lines too.
logger = logging.getLogger("catalog.scheduler.fetch")

QUEUE_LIMIT = 200
CLIENT_JOBS_PER_MINUTE = 10
VISITOR_TTL = timedelta(seconds=120)
STUCK_AFTER = timedelta(minutes=5)
RETENTION = timedelta(days=7)
LIVE_LIMIT = 25
MAX_REQUEUE = timedelta(days=1)  # a "never" wait still retries, at the latest tomorrow
VISITOR_ORIGINS = ("search", "list")


def _active(source: str, phrase: str) -> FetchJob | None:
    return FetchJob.objects.filter(
        source=source, phrase=phrase, status__in=FetchJob.ACTIVE
    ).first()


def enqueue(
    phrase: str,
    origin: str,
    now: datetime,
    sources: Mapping[str, SourceInfo],
    *,
    client_key: str | None,
) -> list[FetchJob]:
    """Queue one job per live source for ``phrase``; returns the active jobs (new or reused)."""
    if not phrase or is_personal(phrase):
        return []
    budget = DbBudget()
    jobs: list[FetchJob] = []
    with transaction.atomic():
        for name, info in sources.items():
            existing = _active(name, phrase)
            if existing is not None:
                jobs.append(existing)
                continue
            if FetchJob.objects.filter(
                source=name,
                phrase=phrase,
                status=FetchJob.Status.SUCCEEDED,
                finished_at__gte=now - info.refresh_after,
            ).exists():
                continue
            if any(budget.blocked(key, now) for key in info.budget_keys):
                continue
            if (
                FetchJob.objects.filter(status=FetchJob.Status.QUEUED).count()
                >= QUEUE_LIMIT
            ):
                continue
            if client_key is not None:
                from web.ratelimit import allow

                if not allow(
                    "fetch-jobs",
                    client_key,
                    CLIENT_JOBS_PER_MINUTE,
                    timedelta(minutes=1),
                ):
                    continue
            try:
                with (
                    transaction.atomic()
                ):  # a savepoint: losing the race keeps the outer block usable
                    job = FetchJob.objects.create(
                        source=name, phrase=phrase, origin=origin, not_before=now
                    )
            except IntegrityError:  # another request inserted the active job first
                job = _active(name, phrase)
                if job is None:
                    continue
            jobs.append(job)
    return jobs


def claim(now: datetime) -> FetchJob | None:
    """Take the next due job (visitor jobs before warm ones); safe with several workers."""
    with transaction.atomic():
        job = (
            FetchJob.objects.select_for_update(skip_locked=True)
            .filter(status=FetchJob.Status.QUEUED, not_before__lte=now)
            .order_by(
                Case(
                    When(origin="warm", then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                "created_at",
                "id",
            )
            .first()
        )
        if job is None:
            return None
        job.status = FetchJob.Status.RUNNING
        job.started_at = now
        job.attempts += 1
        job.save(update_fields=["status", "started_at", "attempts"])
    return job


def _mine(job: FetchJob):
    """The job row while this run still owns it; housekeeping may have reclaimed it."""
    return FetchJob.objects.filter(
        pk=job.pk, status=FetchJob.Status.RUNNING, started_at=job.started_at
    )


def _finish(job: FetchJob, status: str, now: datetime, **fields) -> None:
    _mine(job).update(status=status, finished_at=now, **fields)


def run_job(
    job: FetchJob,
    sources: Mapping[str, LiveSearchSource],
    budget: Budget,
    now: datetime,
) -> None:
    source = sources.get(job.source)
    if source is None:
        _finish(job, FetchJob.Status.SKIPPED, now)
        return
    try:
        result = source.live_search(job.phrase, LIVE_LIMIT, budget)
        count = ingest_live(result, source.name, now)
    except BudgetExhausted as exc:
        _mine(job).update(
            status=FetchJob.Status.QUEUED,
            not_before=now + min(timedelta(seconds=max(exc.wait, 0.0)), MAX_REQUEUE),
            started_at=None,
            attempts=F("attempts") - 1,
        )
        logger.info(
            "fetch job=%s source=%s waiting %.1fs for budget",
            job.pk,
            job.source,
            exc.wait,
        )
        return
    except (
        Exception
    ) as exc:  # the type name only: messages can carry the phrase or URLs
        logger.error(
            "fetch job=%s source=%s failed: %s", job.pk, job.source, type(exc).__name__
        )
        _finish(job, FetchJob.Status.FAILED, now, error=type(exc).__name__[:200])
        return
    _finish(job, FetchJob.Status.SUCCEEDED, now, result_count=count)
    logger.info(
        "fetch job=%s source=%s succeeded products=%s", job.pk, job.source, count
    )


def housekeeping(now: datetime) -> dict[str, int]:
    expired = FetchJob.objects.filter(
        status=FetchJob.Status.QUEUED,
        origin__in=VISITOR_ORIGINS,
        created_at__lte=now - VISITOR_TTL,
    ).update(status=FetchJob.Status.EXPIRED, finished_at=now)
    stuck = FetchJob.objects.filter(
        status=FetchJob.Status.RUNNING, started_at__lte=now - STUCK_AFTER
    )
    failed = stuck.filter(attempts__gte=2).update(
        status=FetchJob.Status.FAILED, error="stuck", finished_at=now
    )
    requeued = stuck.filter(attempts__lt=2).update(
        status=FetchJob.Status.QUEUED, not_before=now, started_at=None
    )
    deleted, _ = FetchJob.objects.filter(
        status__in=FetchJob.TERMINAL, finished_at__lt=now - RETENTION
    ).delete()
    return {
        "expired": expired,
        "requeued": requeued,
        "stuck": failed,
        "deleted": deleted,
    }


def fetch_loop(
    stop: threading.Event,
    *,
    sources: Mapping[str, LiveSearchSource],
    budget: Budget,
    poll: float = 1.0,
    now: Callable[[], datetime] = timezone.now,
) -> None:
    """Claim and run jobs until ``stop`` is set. Nothing raised here ends the thread."""
    try:
        while not stop.is_set():
            job = None
            try:
                job = claim(now())
                if job is None:
                    stop.wait(poll)  # idle: keep the connection; a dead one errors below
                    continue
                run_job(job, sources, budget, now())
                close_old_connections()  # a job's connection is not reused past CONN_MAX_AGE
            except Exception as exc:
                logger.error(
                    "fetch job=%s source=%s failed: %s",
                    job.pk if job else None,
                    job.source if job else None,
                    type(exc).__name__,
                )
                try:
                    close_old_connections()  # an unusable connection is dropped; the next query reconnects
                except Exception:  # pragma: no cover - closing a broken socket
                    pass
                stop.wait(poll)  # never spin on a persistent error (e.g. the DB is down)
    finally:
        connection.close()
