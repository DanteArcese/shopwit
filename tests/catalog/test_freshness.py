from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.test import override_settings

from catalog.freshness import cutoff_for, fresh_offers
from catalog.models import Category, Offer, Product

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def test_cutoff_uses_minimum_of_setting_and_connector():
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=48)):
        assert cutoff_for("walmart", NOW) == NOW - timedelta(hours=24)
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=6)):
        assert cutoff_for("walmart", NOW) == NOW - timedelta(hours=6)


def test_cutoff_unregistered_retailer_uses_setting():
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=48)):
        assert cutoff_for("nobody", NOW) == NOW - timedelta(hours=48)


def make_offer(retailer, item, age_hours):
    cat, _ = Category.objects.get_or_create(
        slug="dairy/milk", defaults={"name": "Milk", "dimension": "volume", "comparison_default": "per_gal"}
    )
    product, _ = Product.objects.get_or_create(identity=f"id-{retailer}-{item}", defaults={"title": "x"})
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal("1.00"),
        currency="USD", url="https://example.com", last_seen_at=NOW - timedelta(hours=age_hours),
        category=cat, title="x",
    )


@pytest.mark.django_db
def test_fresh_offers_hides_stale_per_retailer():
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=48)):
        make_offer("walmart", "fresh", 23)
        make_offer("walmart", "stale", 25)  # connector limit 24h wins over 48h setting
        make_offer("other", "ok", 25)  # unregistered: 48h setting
        make_offer("other", "old", 49)
        ids = set(fresh_offers(NOW).values_list("retailer_item_id", flat=True))
    assert ids == {"fresh", "ok"}
