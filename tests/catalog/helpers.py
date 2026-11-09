"""Shared test helpers for catalog tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from connectors.base import SearchResult
from normalize.types import RawOffer

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
LACTAID = "Lactaid Reduced Fat 2% Milk, 2.8 L"


def raw(title=LACTAID, price="6.58", item="A1", retailer="fake", upc=None, brand="Lactaid"):
    return RawOffer(
        retailer=retailer,
        retailer_item_id=item,
        title=title,
        price=Decimal(price),
        currency="USD",
        url=f"https://example.com/{retailer}/{item}",
        fetched_at=NOW,
        upc=upc,
        brand=brand,
    )


class FakeConnector:
    max_cache_age = timedelta(hours=24)

    def __init__(self, results, name="fake", records_price_history=True, error=None):
        self.name = name
        self.records_price_history = records_price_history
        self.results = results
        self.error = error
        self.queries = []

    def search(self, query, limit=25):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results.get(query, SearchResult(offers=[], skipped=0))


def no_off(upc):
    return None


def make_offer(retailer="walmart", last_seen_at=NOW, item="x1", category=None, price="2.00"):
    """A live offer on its own product."""
    from catalog.models import Offer, Product

    product = Product.objects.create(identity=f"live-{retailer}-{item}", title="Milk", category=category)
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal(price),
        currency="USD", url="https://example.com/x", category=category, title="Milk",
        last_seen_at=last_seen_at, kind="live",
    )


def make_observed_offer(
    chain="Market Basket", store_code="s1", category=None, lat=37.42, lon=-122.08, item="o1",
    on=None, store=None,
):
    """A fresh recorded (Open Prices) offer at a store (created on first use)."""
    from catalog.models import Offer, Product, Store

    store = store or Store.objects.get_or_create(
        code=store_code,
        defaults=dict(
            name=chain, chain_code="op-" + chain.lower().replace(" ", "-"), chain_name=chain,
            country_code="US", osm_type="node", osm_id=abs(hash(store_code)) % 10**6, lat=lat, lon=lon,
        ),
    )[0]
    product = Product.objects.create(identity=f"obs-{store.code}-{item}", title="Milk", category=category)
    return Offer.objects.create(
        product=product, retailer=store.chain_code, retailer_item_id=f"{item}@{store.code}",
        price=Decimal("2.00"), currency="USD", url="https://prices.openfoodfacts.org/prices/1",
        category=category, title="Milk", last_seen_at=NOW, kind="observed",
        observed_on=on or NOW.date(), store=store,
    )
