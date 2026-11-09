from datetime import timedelta

import pytest
from django.conf import settings

from catalog.categories_sync import sync_categories
from catalog.coverage import nearby_categories, stores_we_cover
from catalog.geo import Near
from catalog.models import Category
from tests.catalog.helpers import NOW, make_observed_offer, make_offer

MV = Near(37.42, -122.08, 40.0)


@pytest.fixture
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk"), Category.objects.get(slug="pantry/coffee")


def test_stores_we_cover_from_data(db):
    make_offer(retailer="walmart", last_seen_at=NOW)
    make_offer(retailer="amazon", last_seen_at=NOW - timedelta(days=3), item="x2")  # stale
    make_observed_offer(chain="Market Basket", store_code="s1")
    make_observed_offer(chain="Market Basket", store_code="s2", item="o2")
    make_observed_offer(chain="Market Basket", store_code="s2", item="o3")  # same store counted once
    cov = stores_we_cover(NOW)
    assert cov.live == ["Walmart"]
    assert cov.in_store == [("Market Basket", 2)]


def test_stores_we_cover_excludes_old_observations(db):
    make_observed_offer(on=(NOW - settings.OBSERVED_MAX_AGE - timedelta(days=1)).date())
    assert stores_we_cover(NOW).in_store == []


def test_stores_we_cover_is_empty_without_data(db):
    cov = stores_we_cover(NOW)
    assert cov.live == [] and cov.in_store == []


def test_nearby_categories_orders_and_limits_by_distance(cats):
    milk, bananas = cats
    make_observed_offer(category=milk, item="a")
    make_observed_offer(category=milk, item="b")
    make_observed_offer(category=bananas, item="c")
    make_observed_offer(category=milk, store_code="far", lat=42.36, lon=-71.06, item="d")  # Boston
    result = nearby_categories(NOW, MV)
    assert [(c.slug, n) for c, n in result] == [("dairy/milk", 2), ("pantry/coffee", 1)]


def test_nearby_categories_ignores_unplaced_and_generic(cats):
    milk, _ = cats
    make_observed_offer(category=milk, store_code="nowhere", lat=None, lon=None)
    make_observed_offer(category=None, item="g")  # generic offer
    assert nearby_categories(NOW, MV) == []


def test_nearby_categories_capped_at_eight(db):
    for i in range(10):
        c = Category.objects.create(
            slug=f"c{i}", name=f"Cat {i}", dimension="volume", comparison_default="per_l"
        )
        make_observed_offer(category=c, item=f"i{i}")
    assert len(nearby_categories(NOW, MV)) == 8
