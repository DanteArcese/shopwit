from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

RECORDINGS = "tests/connectors/recordings/walmart"
URL = "http://127.0.0.1:8000/c/dairy/milk/"


class Command(BaseCommand):
    help = "Load categories and recorded Walmart offers for local dogfooding (DEBUG only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("demo_data is for local use only; it refuses to run when DEBUG is off.")
        call_command("sync_categories", stdout=self.stdout)
        call_command("refresh", connector="walmart", recordings=RECORDINGS, stdout=self.stdout)
        self.stdout.write(f"Demo data loaded. Run the server and open {URL}")
