from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from django.test import override_settings

from catalog.freshness import cutoff_for, fresh_observed, fresh_offers
from catalog.models import Category, Offer, Product, Store
from catalog.retailers import available_retailers, display_name

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def test_cutoff_uses_minimum_of_setting_and_connector():
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=48)):
        assert cutoff_for("walmart", NOW) == NOW - timedelta(hours=24)
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=6)):
        assert cutoff_for("walmart", NOW) == NOW - timedelta(hours=6)


def test_cutoff_unregistered_retailer_uses_setting():
    with override_settings(FRESHNESS_CUTOFF=timedelta(hours=48)):
        assert cutoff_for("nobody", NOW) == NOW - timedelta(hours=48)


def make_offer(retailer, item, age_hours, kind="live", observed_on=None, currency="USD", store=None):
    cat, _ = Category.objects.get_or_create(
        slug="dairy/milk", defaults={"name": "Milk", "dimension": "volume", "comparison_default": "per_gal"}
    )
    product, _ = Product.objects.get_or_create(identity=f"id-{retailer}-{item}", defaults={"title": "x"})
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal("1.00"),
        currency=currency, url="https://example.com", last_seen_at=NOW - timedelta(hours=age_hours),
        category=cat, title="x", kind=kind, observed_on=observed_on, store=store,
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


@pytest.mark.django_db
def test_fresh_offers_excludes_observed():
    make_offer("walmart", "live", 1)
    make_offer("op-safeway", "obs", 1, kind="observed", observed_on=NOW.date())
    assert set(fresh_offers(NOW).values_list("retailer_item_id", flat=True)) == {"live"}


@pytest.mark.django_db
def test_fresh_observed_by_date_not_sync_time():
    with override_settings(OBSERVED_MAX_AGE=timedelta(days=90)):
        make_offer("op-safeway", "edge", 30 * 24, kind="observed", observed_on=(NOW - timedelta(days=90)).date())
        make_offer("op-safeway", "old", 30 * 24, kind="observed", observed_on=(NOW - timedelta(days=91)).date())
        make_offer("walmart", "live", 1)
        ids = set(fresh_observed(NOW).values_list("retailer_item_id", flat=True))
    assert ids == {"edge"}


@pytest.mark.django_db
def test_currency_filter():
    with override_settings(SITE_CURRENCY="USD"):
        make_offer("walmart", "usd", 1)
        make_offer("walmart", "eur", 1, currency="EUR")
        make_offer("op-safeway", "obs-usd", 1, kind="observed", observed_on=NOW.date())
        make_offer("op-safeway", "obs-eur", 1, kind="observed", observed_on=NOW.date(), currency="EUR")
        assert set(fresh_offers(NOW).values_list("retailer_item_id", flat=True)) == {"usd"}
        assert set(fresh_observed(NOW).values_list("retailer_item_id", flat=True)) == {"obs-usd"}


@pytest.mark.django_db
def test_available_retailers_lists_chains_once():
    for n, city in ((1, "Mountain View"), (2, "Sunnyvale")):
        store = Store.objects.create(
            code=f"osm-n{n}", name="Safeway", chain_code="op-safeway", chain_name="Safeway", city=city,
            country_code="US", osm_type="node", osm_id=n,
        )
        make_offer("op-safeway", f"upc@osm-n{n}", 1, kind="observed", observed_on=NOW.date(), store=store)
    make_offer("walmart", "w", 1)
    assert available_retailers(NOW) == ["walmart", "op-safeway"]
    assert display_name("op-safeway") == "Safeway (in-store prices)"
    assert display_name("op-unknown") == "op-unknown"


@pytest.mark.django_db
def test_category_store_list_ignores_generic_offers_on_a_curated_product():
    """A generic observed offer sharing a curated product adds no chain to that category's stores."""
    milk = Category.objects.create(slug="dairy/milk", name="Milk", dimension="volume", comparison_default="per_gal")
    product = Product.objects.create(identity="upc:1", title="Milk", category=milk)
    for chain, category in (("op-curated", milk), ("op-generic", None)):
        store = Store.objects.create(
            code=f"osm-{chain}", name=chain, chain_code=chain, chain_name=chain.title(),
            country_code="US", osm_type="node", osm_id=len(chain),
        )
        Offer.objects.create(
            product=product, retailer=chain, retailer_item_id=f"1@{chain}", price=Decimal("3"), currency="USD",
            url="https://example.com/", last_seen_at=NOW, kind="observed", observed_on=NOW.date(),
            store=store, category=category,
        )
    assert available_retailers(NOW, milk) == ["op-curated"]
    assert set(available_retailers(NOW)) == {"op-curated", "op-generic"}
