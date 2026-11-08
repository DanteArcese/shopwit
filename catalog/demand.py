"""Anonymous search demand: counting, pruning, "curate next" and warm refresh.

A demand row holds only the normalized phrase, a count, the number of distinct days it was
searched and the last day. No user, IP, session or request detail is ever stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import QuerySet

from connectors.live import SourceInfo
from normalize.generic import is_personal

from .fetch import QUEUE_LIMIT, enqueue
from .models import FetchJob, SearchDemand

LIST_MIN_DAYS = 3
PRUNE_AFTER = timedelta(days=30)
MAX_PHRASES = 50_000
WARM_TOP = 50
PHRASE_MAX = SearchDemand._meta.get_field("phrase").max_length
# When this process last ran a warm round (per process: a restarted worker warms once more,
# which the jobs' own refresh_after check keeps cheap).
_last_warm_round: datetime | None = None

_UPSERT = f"""
INSERT INTO {SearchDemand._meta.db_table} (phrase, count, days_seen, last_seen)
VALUES (%s, 1, 1, %s)
ON CONFLICT (phrase) DO UPDATE SET
    count = {SearchDemand._meta.db_table}.count + 1,
    days_seen = {SearchDemand._meta.db_table}.days_seen
        + (EXCLUDED.last_seen > {SearchDemand._meta.db_table}.last_seen)::int,
    last_seen = GREATEST(EXCLUDED.last_seen, {SearchDemand._meta.db_table}.last_seen)
"""


def record_demand(phrase: str, today: date) -> None:
    """Count one genuine search of ``phrase`` (already normalized). No-op if empty or personal."""
    if not phrase or len(phrase) > PHRASE_MAX or is_personal(phrase):
        return
    with transaction.atomic(), connection.cursor() as cursor:  # a failure leaves no broken transaction
        cursor.execute(_UPSERT, [phrase, today])


def prune_demand(today: date) -> int:
    """Drop every phrase not searched for PRUNE_AFTER, then the lowest-ranked rows above MAX_PHRASES."""
    deleted, _ = SearchDemand.objects.filter(last_seen__lt=today - PRUNE_AFTER).delete()
    excess = SearchDemand.objects.count() - MAX_PHRASES
    if excess > 0:
        ids = list(
            SearchDemand.objects.order_by("days_seen", "last_seen", "id").values_list(
                "id", flat=True
            )[:excess]
        )
        extra, _ = SearchDemand.objects.filter(id__in=ids).delete()
        deleted += extra
    return deleted


def curate_next() -> QuerySet[SearchDemand]:
    return SearchDemand.objects.filter(days_seen__gte=LIST_MIN_DAYS).order_by(
        "-days_seen", "-count", "-last_seen", "phrase"
    )


def warm_due(now: datetime) -> bool:
    """A round is due WARM_INTERVAL after the last one, even one that queued nothing."""
    if _last_warm_round is None:
        return curate_next().exists()
    return _last_warm_round <= now - settings.WARM_INTERVAL


def warm_round(now: datetime, sources: Mapping[str, SourceInfo]) -> int:
    """Queue warm jobs for the top phrases; the previous round's unstarted jobs expire.

    At most QUEUE_LIMIT / 2 jobs, so visitors always find room in the queue.
    """
    global _last_warm_round
    FetchJob.objects.filter(origin="warm", status=FetchJob.Status.QUEUED).update(
        status=FetchJob.Status.EXPIRED, finished_at=now
    )
    created = 0
    taken = 0
    cap = QUEUE_LIMIT // 2
    for phrase in curate_next().values_list("phrase", flat=True):
        if taken >= WARM_TOP or created + len(sources) > cap:
            break
        if is_personal(phrase):
            continue
        taken += 1
        for job in enqueue(phrase, "warm", now, sources, client_key=None):
            if job.origin == "warm" and job.status == FetchJob.Status.QUEUED and (
                job.created_at is None or job.created_at != now
            ):
                FetchJob.objects.filter(pk=job.pk).update(created_at=now)
                created += 1
    _last_warm_round = now
    return created
