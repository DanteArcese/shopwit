from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.sample import purge_sample, sample_item_ids

DEFAULT_DIR = settings.BASE_DIR / "tests" / "connectors" / "recordings" / "walmart"


class Command(BaseCommand):
    help = "Delete recorded Walmart sample offers (and products left empty). Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--recordings", default=str(DEFAULT_DIR), help="Recordings DIR to read item ids from")
        parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry run)")

    def handle(self, *args, **options):
        ids = sample_item_ids(options["recordings"])
        offers, products = purge_sample(ids, options["apply"])
        tail = "deleted" if options["apply"] else "(dry run: nothing deleted)"
        self.stdout.write(f"offers={offers} products={products} {tail}")
