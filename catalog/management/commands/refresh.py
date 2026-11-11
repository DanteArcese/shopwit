from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.ingest import ingest
from catalog.models import Category, IngestionRun
from catalog.outliers import flag_outliers_safely
from connectors import registry
from connectors.recording import recorded_transport
from connectors.walmart import WalmartConnector


def _recorded_walmart(directory: str) -> tuple[WalmartConnector, httpx.Client]:
    """Offline connector: placeholder credentials, throwaway key, recorded responses."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    client = httpx.Client(transport=recorded_transport(Path(directory)))
    return WalmartConnector("recorded", pem, "1", client=client), client


class Command(BaseCommand):
    help = "Refresh categories now, ignoring the refresh interval."

    def add_arguments(self, parser):
        parser.add_argument("--connector", help="Connector name (default: all available)")
        parser.add_argument("--category", help="Category slug (default: all active)")
        parser.add_argument("--recordings", help="Serve recorded responses from DIR (walmart only)")

    def handle(self, *args, **options):
        name, slug, recordings = options["connector"], options["category"], options["recordings"]
        client = None
        if recordings:
            if not settings.DEBUG:
                raise CommandError("--recordings loads sample data; it refuses to run when DEBUG is off.")
            name = name or "walmart"
            if name != "walmart":
                raise CommandError("--recordings is supported only with --connector walmart")
            connector, client = _recorded_walmart(recordings)
            connectors = {"walmart": connector}
        else:
            if name and name not in registry.SPECS:
                raise CommandError(
                    f"Unknown connector {name!r}; valid connectors: {', '.join(sorted(registry.SPECS))}"
                )
            connectors = registry.available()
            if name:
                if name not in connectors:
                    needs = registry.SPECS[name].required.values()
                    raise CommandError(
                        f"Connector {name!r} is not available; required environment variables: "
                        + ", ".join(needs)
                    )
                connectors = {name: connectors[name]}
            if not connectors:
                needs = sorted({v for s in registry.SPECS.values() for v in s.required.values()})
                raise CommandError(
                    "No connectors available. Set environment variables: " + ", ".join(needs)
                )

        try:
            categories = Category.objects.filter(active=True).order_by("slug")
            if slug:
                categories = categories.filter(slug=slug)
                if not categories.exists():
                    raise CommandError(f"No active category {slug!r}")
            statuses = []
            for connector_name, connector in connectors.items():
                for category in categories:
                    run = ingest(connector, category, timezone.now())
                    statuses.append(run.status)
                    self.stdout.write(
                        f"{connector_name} {category.slug} status={run.status} fetched={run.fetched} "
                        f"matched={run.matched} fully_parsed={run.fully_parsed} "
                        f"unresolved={run.unresolved} skipped={run.skipped}"
                    )
            if IngestionRun.Status.SUCCEEDED in statuses:
                flag_outliers_safely(timezone.now())  # unit prices changed (#127)
            if statuses and IngestionRun.Status.SUCCEEDED not in statuses:
                raise CommandError("All ingestion runs failed")
        finally:
            if client is not None:
                client.close()
