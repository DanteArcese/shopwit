"""Write the Open Prices observations (ODbL data) as gzip JSONL.

Only Open Prices data and each observed offer's own normalization is read; nothing
comes from live offers, products or unit_prices.
"""

import gzip
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import Offer, OpenPriceObservation

PRICE_URL = "https://prices.openfoodfacts.org/prices/"


class Command(BaseCommand):
    help = "Export Open Prices observations and their normalization as gzip JSONL (ODbL)."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Output path (.jsonl.gz)")

    def handle(self, *args, **options):
        normalized = {}
        offers = Offer.objects.filter(
            kind=Offer.Kind.OBSERVED, category__isnull=False
        ).select_related("category")
        for o in offers:
            if o.url.startswith(PRICE_URL):
                normalized[o.url[len(PRICE_URL):]] = {
                    "category": o.category.slug,
                    "attributes": o.attributes,
                    "measures": o.measures,
                }
        header = {
            "license": "ODbL-1.0",
            "source": "https://prices.openfoodfacts.org",
            "generated_at": timezone.now().isoformat(),
            "method": "https://github.com/DanteArcese/unitpricer",
        }
        count = 0
        with gzip.open(options["out"], "wt", encoding="utf-8") as fh:
            fh.write(json.dumps(header) + "\n")
            qs = OpenPriceObservation.objects.select_related("store").order_by("price_id")
            for ob in qs.iterator():
                s = ob.store
                row = {
                    "price_id": ob.price_id,
                    "upc": ob.upc,
                    "product_name": ob.product_name,
                    "brands": ob.brands,
                    "quantity": ob.quantity,
                    "labels": ob.labels,
                    "price": str(ob.price),
                    "currency": ob.currency,
                    "is_sale": ob.is_sale,
                    "observed_on": ob.observed_on.isoformat(),
                    "proof_type": ob.proof_type,
                    "store": {
                        "code": s.code, "name": s.name, "city": s.city, "postcode": s.postcode,
                        "country_code": s.country_code, "osm_type": s.osm_type, "osm_id": s.osm_id,
                    },
                }
                norm = normalized.get(str(ob.price_id))
                if norm:
                    row["normalized"] = norm
                fh.write(json.dumps(row) + "\n")
                count += 1
        self.stdout.write(f"wrote {count} observations to {options['out']}")
