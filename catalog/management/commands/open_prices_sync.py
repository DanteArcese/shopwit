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

    def handle(self, *args, **options):
        if options["recordings"]:
            http = httpx.Client(
                transport=recorded_transport(Path(options["recordings"])),
                headers={"User-Agent": USER_AGENT},
            )
            client = OpenPricesClient(http, sleep=lambda s: None)
        else:
            client = OpenPricesClient()
        try:
            run = sync_open_prices(client, timezone.now())
        finally:
            client.close()
        self.stdout.write(
            f"status={run.status} fetched={run.fetched} matched={run.matched} "
            f"fully_parsed={run.fully_parsed} unresolved={run.unresolved} skipped={run.skipped}"
        )
        if run.status != IngestionRun.Status.SUCCEEDED:
            raise CommandError(f"Open Prices sync failed: {run.error}")
