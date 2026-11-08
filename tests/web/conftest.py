import pytest
from django.conf import settings

from catalog.categories_sync import sync_categories
from catalog.ingest import ingest
from catalog.models import Category
from connectors.base import SearchResult
from tests.catalog.helpers import NOW, FakeConnector, no_off, raw


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr("web.views.now", lambda: NOW)
    return NOW


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


@pytest.fixture
def load(milk, frozen_now):
    def _load(offers, category=None):
        category = category or milk
        conn = FakeConnector({q: SearchResult(offers=offers, skipped=0) for q in category.search})
        ingest(conn, category, NOW, off_lookup=no_off)

    return _load


@pytest.fixture
def milk_rows(load):
    load([
        raw(title="Acme Organic 2% Milk, 1 L", price="2.00", item="a", brand="Acme"),
        raw(title="Bravo Whole Milk, 1 L", price="1.00", item="b", brand="Bravo"),
        raw(title="Cargo Milk, 1 L", price="1.50", item="c", brand="Cargo"),  # fat unknown
        raw(title="Mystery Whole Milk", price="3.00", item="d", brand="Mystery"),  # no size
    ])
