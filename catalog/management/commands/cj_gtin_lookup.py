"""Look up CJ Affiliate offers for the barcodes of products we already show (#111).

The worker runs the same step once a day after the Open Prices sync. Requests draw from
the shared "cj" budget, so a manual run counts toward the day's cap.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.budget import DbBudget, seed_budgets
from catalog.cj_gtin import DEFAULT_LIMIT, run_backfill
from catalog.models import IngestionRun
from connectors import registry


class Command(BaseCommand):
    help = "Look up CJ offers by barcode for fresh observed offers (joined advertisers only)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Most barcodes to look up (default 300)")

    def handle(self, *args, **options):
        connectors = registry.available()
        try:
            cj = connectors.get("cj")
            if cj is None:
                raise CommandError(
                    "CJ is not configured: set CJ_PERSONAL_ACCESS_TOKEN and CJ_PUBLISHER_ID."
                )
            seed_budgets()
            run, stats = run_backfill(cj, DbBudget(), timezone.now(), max(0, options["limit"]))
        finally:
            for connector in connectors.values():
                close = getattr(connector, "close", None)
                if close is not None:
                    close()
        self.stdout.write(f"status={run.status} {stats.line()}" + (f" note={run.error}" if run.error else ""))
        if run.status != IngestionRun.Status.SUCCEEDED:
            raise CommandError(f"CJ barcode lookup failed: {run.error}")
