from datetime import timedelta
from pathlib import Path

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.models import IngestionRun
from catalog.observed import sync_open_prices
from connectors.open_prices import USER_AGENT, OpenPricesClient
from connectors.recording import recorded_transport


class Command(BaseCommand):
    help = "Pull the Open Prices window now, ignoring the sync interval and UNITPRICER_OPEN_PRICES."

    def add_arguments(self, parser):
        parser.add_argument("--recordings", help="Serve recorded responses from DIR")
        parser.add_argument(
            "--force", action="store_true", help="Run even if a sync started in the last hour is still RUNNING"
        )
        parser.add_argument(
            "--allow-deletions",
            action="store_true",
            help="Delete rows missing from the pull even if it is suspiciously small",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        if not options["force"] and IngestionRun.objects.filter(
            connector="openprices",
            status=IngestionRun.Status.RUNNING,
            started_at__gte=now - timedelta(hours=1),
        ).exists():
            raise CommandError(
                "An Open Prices sync started within the last hour is still running "
                "(the worker may be running it). Use --force to run anyway."
            )
        if options["recordings"]:
            http = httpx.Client(
                transport=recorded_transport(Path(options["recordings"])),
                headers={"User-Agent": USER_AGENT},
            )
            client = OpenPricesClient(http, sleep=lambda s: None)
        else:
            client = OpenPricesClient()
        try:
            run = sync_open_prices(client, now, allow_deletions=options["allow_deletions"])
        finally:
            client.close()
        self.stdout.write(
            f"status={run.status} fetched={run.fetched} matched={run.matched} "
            f"fully_parsed={run.fully_parsed} unresolved={run.unresolved} skipped={run.skipped}"
        )
        if run.status != IngestionRun.Status.SUCCEEDED:
            raise CommandError(f"Open Prices sync failed: {run.error}")
