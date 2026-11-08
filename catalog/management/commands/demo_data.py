from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from catalog.models import IngestionRun

RECORDINGS = settings.BASE_DIR / "tests" / "connectors" / "recordings" / "walmart"
PATH = "/c/dairy/milk/"


class Command(BaseCommand):
    help = "Load categories and recorded Walmart offers for local dogfooding (DEBUG only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("demo_data is for local use only; it refuses to run when DEBUG is off.")
        call_command("sync_categories", stdout=self.stdout)
        before = IngestionRun.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
        try:
            call_command("refresh", connector="walmart", recordings=str(RECORDINGS), stdout=self.stdout)
        except CommandError:
            pass  # reported below from the run records
        runs = IngestionRun.objects.filter(pk__gt=before)
        loaded = sorted(r.category_slug for r in runs if r.status == IngestionRun.Status.SUCCEEDED)
        failed = runs.exclude(status=IngestionRun.Status.SUCCEEDED).count()
        if not loaded:
            raise CommandError("No category loaded from the recordings.")
        self.stdout.write(
            f"Loaded {', '.join(loaded)}; {failed} categories have no recordings. "
            f"Start the server (runserver) and open {PATH} on it."
        )
