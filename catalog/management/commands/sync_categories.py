from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.categories_sync import sync_categories


class Command(BaseCommand):
    help = "Sync category definitions from the YAML files into the database."

    def handle(self, *args, **options):
        result = sync_categories(settings.CATEGORIES_DIR)
        self.stdout.write(
            f"created={result.created} updated={result.updated} deactivated={result.deactivated}"
        )
