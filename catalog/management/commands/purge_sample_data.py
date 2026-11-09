from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.sample import purge_sample, sample_items

DEFAULT_DIR = settings.BASE_DIR / "tests" / "connectors" / "recordings" / "walmart"


class Command(BaseCommand):
    help = (
        "Delete recorded Walmart sample offers, unpin and drop emptied products, re-merge the rest. "
        "Dry run unless --apply. Run it BEFORE enabling the real Walmart key."
    )

    def add_arguments(self, parser):
        parser.add_argument("--recordings", default=str(DEFAULT_DIR), help="Recordings DIR to read item ids from")
        parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry run)")

    def handle(self, *args, **options):
        items = sample_items(options["recordings"])
        offers, products, pins, remerged = purge_sample(items, options["apply"])
        tail = "deleted" if options["apply"] else "(dry run: nothing deleted)"
        self.stdout.write(f"offers={offers} products={products} pins_cleared={pins} products_remerged={remerged} {tail}")
