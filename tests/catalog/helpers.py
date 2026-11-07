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
