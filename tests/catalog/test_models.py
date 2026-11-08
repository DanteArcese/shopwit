from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import Offer, Product, ReviewItem, Store


def make_product(identity="p1"):
    return Product.objects.create(identity=identity, title="Milk")


def make_offer(product, **kw):
    fields = dict(
        product=product,
        retailer="walmart",
        retailer_item_id="1",
        price=Decimal("3.99"),
        currency="USD",
        url="https://example.com/1",
        last_seen_at=timezone.now(),
    )
    fields.update(kw)
    return Offer.objects.create(**fields)


@pytest.mark.django_db
def test_offer_unique_per_retailer_item():
    product = make_product()
    make_offer(product)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_offer(product)
    make_offer(product, retailer="target")


@pytest.mark.django_db
def test_one_open_review_item_per_detail():
    product = make_product()
    first = ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")
    first.status = ReviewItem.Status.RESOLVED
    first.resolved_at = timezone.now()
    first.save()
    ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")


def test_store_display():
    assert Store(name="Safeway", city="Mountain View").display == "Safeway, Mountain View"
    assert Store(name="Safeway", city=None).display == "Safeway"


@pytest.mark.django_db
def test_offer_defaults_live():
    offer = make_offer(make_product())
    assert (offer.kind, offer.observed_on, offer.is_sale, offer.store) == ("live", None, False, None)
