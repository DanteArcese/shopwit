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


def test_nearby_candidates_only_stores_with_fresh_offers(cats):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from catalog.models import Store

    milk, _ = cats
    make_observed_offer(category=milk, store_code="busy")
    stale = make_observed_offer(category=milk, store_code="stale", item="old",
                                on=(NOW - settings.OBSERVED_MAX_AGE - timedelta(days=1)).date())
    Store.objects.bulk_create(
        Store(code=f"empty{i}", name="E", chain_code="op-e", chain_name="E", country_code="US",
              osm_type="node", osm_id=900000 + i, lat=37.42, lon=-122.08)
        for i in range(40)
    )
    with CaptureQueriesContext(connection) as ctx:
        result = nearby_categories(NOW, MV)
    assert [(c.slug, n) for c, n in result] == [("dairy/milk", 1)]
    assert len(ctx) <= 3
    # Only the one store with fresh recorded prices is a candidate: no IN list of empty stores.
    store_ids = {str(pk) for pk in Store.objects.filter(code__startswith="empty").values_list("pk", flat=True)}
    store_ids.add(str(stale.store_id))
    for q in ctx.captured_queries:
        for lst in __import__("re").findall(r"IN \(([^()]*)\)", q["sql"]):
            assert not store_ids & {x.strip() for x in lst.split(",")}, q["sql"]


def test_stores_we_cover_merges_chains_differing_by_case(db):
    make_observed_offer(chain="Aldi", store_code="a1")
    for i in range(5):
        make_observed_offer(chain="ALDI", store_code=f"b{i}", item=f"i{i}")
    # make_observed_offer derives the same chain_code for both spellings
    assert stores_we_cover(NOW).in_store == [("ALDI", 5 + 1)]


def test_stores_we_cover_case_tie_is_deterministic(db):
    make_observed_offer(chain="Aldi", store_code="a1")
    make_observed_offer(chain="ALDI", store_code="b1", item="i2")
    assert stores_we_cover(NOW).in_store == [("ALDI", 2)]  # tie: name order, not pk order
