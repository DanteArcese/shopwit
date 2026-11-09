"""Look up Open Food Facts data for observed offers' barcodes now (#88).

The worker runs the same step after each successful Open Prices sync. Requests draw
from the shared "off-product" budget (1 per second, a daily cap), so a manual run
counts toward the day's cap.
"""

from pathlib import Path

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.budget import DbBudget, seed_budgets
from catalog.models import IngestionRun
from catalog.observed_off import backfill_sizes
from connectors.recording import recorded_transport
from enrichment.off import USER_AGENT


class Command(BaseCommand):
    help = (
        "Fetch Open Food Facts products for observed offers' barcodes that have no cached "
        "entry or no size, then normalize those offers again."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Most barcodes to look up (default: the daily cap, boosted in the first week)")
        parser.add_argument("--recordings", help="Serve recorded responses from DIR")

    def handle(self, *args, **options):
        seed_budgets()
        client = None
        if options["recordings"]:
            client = httpx.Client(
                transport=recorded_transport(Path(options["recordings"])),
                headers={"User-Agent": USER_AGENT},
            )
        try:
            run, stats = backfill_sizes(
                timezone.now(), DbBudget(), limit=options["limit"], client=client
            )
        finally:
            if client is not None:
                client.close()
        self.stdout.write(f"status={run.status} {stats.line()}" + (f" note={run.error}" if run.error else ""))
        if run.status != IngestionRun.Status.SUCCEEDED:
            raise CommandError(f"OFF size lookup failed: {run.error}")
