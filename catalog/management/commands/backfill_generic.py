"""Recompute the generic offer fields (measure, unit price, labels, category path).

Live offers keep no size text, so they are computed from the title and the OFF
quantity; observed offers also use their latest observation's quantity. Live
offers get exact values on their next refresh. Safe to run repeatedly: by
default offers that already have a generic measure (a dimension, or a reason it
is unknown) are skipped; ``--all`` recomputes every offer.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

import enrichment.off
from catalog.models import Offer, OpenPriceObservation
from normalize.generic import generic_measure, generic_unit_price, off_category_path


def _observation(offer: Offer):
    if offer.kind != Offer.Kind.OBSERVED or offer.store_id is None:
        return None
    upc = offer.retailer_item_id.split("@", 1)[0]
    return (
        OpenPriceObservation.objects.filter(store_id=offer.store_id, upc=upc)
        .order_by("-observed_on", "-price_id")
        .first()
    )


def recompute(offer: Offer, off_lookup=enrichment.off.lookup) -> bool:
    """Set the generic fields on ``offer``; returns True when it saved.

    The row is re-read under a lock first, so a concurrent ingest's price or
    category path is never overwritten with a stale value.
    """
    with transaction.atomic():
        fresh = Offer.objects.select_for_update().select_related("product").filter(pk=offer.pk).first()
        if fresh is None:
            return False
        return _recompute_locked(fresh, off_lookup)


def _recompute_locked(offer: Offer, off_lookup) -> bool:
    off = off_lookup(offer.product.upc)
    obs = _observation(offer)
    quantity = (off.quantity if off else None) or (obs.quantity if obs else None) or ""
    measure = generic_measure(
        [(obs.quantity if obs else None) or "", offer.title, quantity],
        off.quantity_unit if off else None,
    )
    if off is not None:
        labels = sorted(off.labels)
        path = off_category_path(off.categories)
    elif obs is not None:
        labels = sorted(obs.labels)
        path = off_category_path(obs.categories)
    else:
        labels, path = [], None
    offer.measure_dim = measure.dimension
    offer.measure_amount = measure.amount
    offer.measure_reason = measure.reason
    offer.generic_unit_price = generic_unit_price(offer.price, measure)
    offer.labels = labels
    offer.category_path = offer.category_path or path  # a retailer path is not recoverable here
    offer.save(
        update_fields=[
            "measure_dim", "measure_amount", "measure_reason", "generic_unit_price",
            "labels", "category_path", "updated_at",
        ]
    )
    return True


class Command(BaseCommand):
    help = "Recompute generic measure, unit price, labels and category path for every offer."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=1000)
        parser.add_argument(
            "--all", action="store_true", help="recompute offers that already have a generic measure"
        )

    def handle(self, *args, **options):
        batch = max(1, options["batch"])
        updated = 0
        last_pk = 0
        while True:
            qs = Offer.objects.filter(pk__gt=last_pk)
            if not options["all"]:
                qs = qs.filter(measure_dim__isnull=True, measure_reason__isnull=True)
            offers = list(qs.select_related("product").order_by("pk")[:batch])
            if not offers:
                break
            for offer in offers:
                updated += recompute(offer)
            last_pk = offers[-1].pk
        self.stdout.write(f"updated={updated}")
