from django.core.management.base import BaseCommand

from enrichment import off


class Command(BaseCommand):
    help = "Look up products with a UPC but no cached Open Food Facts entry, via the per-UPC API."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        stats = off.backfill(limit=options["limit"])
        self.stdout.write(" ".join(f"{k}={v}" for k, v in stats.items()))
