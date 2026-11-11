"""Hold unit prices far below their category's median from ranking (#127).

The worker runs the same pass after ingest runs and the Open Prices sync; this runs it
by hand (e.g. after a deploy). Idempotent.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.outliers import flag_unit_price_outliers


class Command(BaseCommand):
    help = "Flag unit-price outliers (below a tenth of the category median) and sync their review items."

    def handle(self, *args, **options):
        stats = flag_unit_price_outliers(timezone.now())
        total = 0
        for slug, counts in stats.items():
            held = {c: n for c, n in counts.items() if n}
            if held:
                total += sum(held.values())
                self.stdout.write(f"{slug} " + " ".join(f"{c}={n}" for c, n in held.items()))
        self.stdout.write(f"held={total} categories={len(stats)}")
