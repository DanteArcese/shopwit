import random
import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.generic import group_products, match_offers
from catalog.models import Offer, Product
from normalize.text import fold

RETAILER = "bench"
ADJ = ["organic", "whole", "fresh", "classic", "creamy", "sparkling", "crunchy", "frozen", "sweet", "spicy"]
NOUN = ["milk", "water", "rice", "beans", "soda", "juice", "bread", "cheese", "yogurt", "pasta", "butter", "tea"]
BRAND = ["Acme", "Oatly", "Goya", "Kirkland", "Heinz", "Barilla", "Dole", "Tropicana"]
DIMS = [("volume", 1000.0), ("mass", 500.0), ("oz_labeled", 32.0), ("count", 12.0)]
PHRASES = [f"{a} {n}" for a in ADJ[:5] for n in NOUN[:10]]  # 50 phrases


class Command(BaseCommand):
    help = "Benchmark generic search over synthetic offers (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--offers", type=int, default=100000)
        parser.add_argument("--keep", action="store_true")

    def handle(self, *args, offers, keep, **options):
        if not settings.DEBUG:
            raise CommandError("bench_search is for local use only; it refuses to run when DEBUG is off.")
        rng = random.Random(65)
        now = datetime.now(timezone.utc)
        try:
            products, rows = [], []
            for i in range(offers):
                title = f"{rng.choice(BRAND)} {rng.choice(ADJ)} {rng.choice(NOUN)} {i % 997}"
                products.append(Product(identity=f"{RETAILER}:{i}", title=title))
            Product.objects.bulk_create(products, batch_size=5000)
            ids = Product.objects.filter(identity__startswith=f"{RETAILER}:").values_list("identity", "id", "title")
            for identity, pid, title in ids:
                i = int(identity.split(":")[1])
                dim, amount = DIMS[rng.randrange(4)]
                price = Decimal(rng.randrange(100, 2000)) / 100
                rows.append(
                    Offer(
                        product_id=pid, retailer=RETAILER, retailer_item_id=str(i), price=price,
                        currency=settings.SITE_CURRENCY, url="https://example.com/", title=title,
                        last_seen_at=now, measure_dim=dim, measure_amount=amount,
                        generic_unit_price=(price / Decimal(str(amount))).quantize(Decimal("0.000001")),
                    )
                )
            Offer.objects.bulk_create(rows, batch_size=5000)
            times = []
            for phrase in PHRASES:
                for _ in range(5):
                    start = time.perf_counter()
                    group_products(match_offers(fold(phrase), now).offers)
                    times.append((time.perf_counter() - start) * 1000)
            times.sort()
            p95 = times[int(len(times) * 0.95) - 1]
            self.stdout.write(f"p50={statistics.median(times):.0f} p95={p95:.0f}")
        finally:
            if not keep:
                Product.objects.filter(identity__startswith=f"{RETAILER}:").delete()
