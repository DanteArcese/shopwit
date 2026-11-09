import pytest
from django.conf import settings

from catalog.categories_sync import sync_categories
from catalog.ingest import ingest
from catalog.models import Category
from connectors.base import SearchResult
from tests.catalog.helpers import NOW, FakeConnector, no_off


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


@pytest.fixture
def tp(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="household/toilet-paper")


@pytest.fixture
def load():
    def _load(category, offers, now=NOW):
        conn = FakeConnector({q: SearchResult(offers=offers, skipped=0) for q in category.search})
        ingest(conn, category, now, off_lookup=no_off)

    return _load
