from pathlib import Path

import pytest
from django.conf import settings
from django.http import QueryDict

from catalog.generic import (
    GenericFilters,
    apply_filters,
    facets,
    load_label_allowlist,
    parse_generic_filters,
)
from catalog.ingest import ingest
from catalog.models import Category, Offer
from connectors.base import SearchResult
from normalize import load_store_brands
from normalize.types import OffProduct
from tests.catalog.helpers import NOW, FakeConnector, raw
from tests.catalog.test_generic_match import make_generic_offer

ALLOW = {"en:organic": "Organic", "en:vegan": "Vegan"}
STORE_BRANDS = load_store_brands(settings.STORE_BRANDS_FILE)


def params(qs=""):
    return QueryDict(qs)


def parse(qs="", system="imperial"):
    return parse_generic_filters(params(qs), system, ALLOW)


def names(offers):
    return sorted(o.title for o in offers)


def facet_map(offers, qs="", system="imperial"):
    f = parse(qs, system)
    return {x.name: x for x in facets(offers, f, STORE_BRANDS, ALLOW, system)}


@pytest.mark.django_db
def test_facet_needs_two_values():
    a = make_generic_offer("Oat A", "3", dim="volume", amount=1000, brand="Oatly")
    assert "brand" not in facet_map([a])
    nobrand = make_generic_offer("Oat B", "3", dim="volume", amount=1000)
    assert "brand" not in facet_map([a, nobrand])  # a product with no brand is not a value
    b = make_generic_offer("Oat C", "3", dim="volume", amount=1000, brand="Alpro")
    brand = facet_map([a, nobrand, b])["brand"]
    assert {v for v, _, _ in brand.options} == {"oatly", "alpro"}
    assert not brand.active
    # active filter with a single value left still shows
    only = [a]
    shown = facet_map(only, "brand=oatly")["brand"]
    assert shown.active and [v for v, _, _ in shown.options] == ["oatly"]
    # label offered only when some have it and some don't
    org = make_generic_offer("Org", "3", dim="volume", amount=1000, labels=["en:organic"])
    assert "labels" not in facet_map([org])
    plain = make_generic_offer("Plain", "3", dim="volume", amount=1000)
    labels = facet_map([org, plain])["labels"]
    assert [v for v, _, _ in labels.options] == ["en:organic"]
    assert "from Open Food Facts" in labels.options[0][1]


@pytest.mark.django_db
def test_brand_spelling_most_common_then_alphabetical():
    offers = [
        make_generic_offer("Q1", "3", dim="volume", amount=1000, brand="OATLY", retailer="a"),
        make_generic_offer("Q2", "3", dim="volume", amount=1000, brand="Oatly", retailer="a"),
        make_generic_offer("Q3", "3", dim="volume", amount=1000, brand="Oatly", retailer="b"),
        make_generic_offer("Q4", "3", dim="volume", amount=1000, brand="Alpro"),
    ]
    options = {v: (label, n) for v, label, n in facet_map(offers)["brand"].options}
    assert options["oatly"] == ("Oatly", 3)
    tie = [
        make_generic_offer("T1", "3", dim="volume", amount=1000, brand="ZED", retailer="a"),
        make_generic_offer("T2", "3", dim="volume", amount=1000, brand="Zed", retailer="a"),
        make_generic_offer("T3", "3", dim="volume", amount=1000, brand="Other"),
    ]
    assert {v: label for v, label, _ in facet_map(tie)["brand"].options}["zed"] == "ZED"


@pytest.mark.django_db
def test_facet_computed_over_other_filters():
    a = make_generic_offer("A", "3", dim="volume", amount=1000, brand="Oatly", labels=["en:vegan"])
    b = make_generic_offer("B", "3", dim="volume", amount=1000, brand="Alpro")
    f = facet_map([a, b], "label=en:vegan")
    # the brand facet only sees vegan products, so one brand and not active
    assert "brand" not in f
    # the labels facet ignores its own filter, so it still shows
    assert f["labels"].active


@pytest.mark.django_db
def test_unknown_excluded_under_filter():
    a = make_generic_offer("A", "3", dim="volume", amount=1000, brand="Oatly")
    b = make_generic_offer("B", "3", dim="volume", amount=1000)
    assert names(apply_filters([a, b], parse(), STORE_BRANDS)) == ["A", "B"]
    assert names(apply_filters([a, b], parse("brand=oatly"), STORE_BRANDS)) == ["A"]
    assert names(apply_filters([a, b], parse("brand_tier=name_brand"), STORE_BRANDS)) == ["A"]
    g = make_generic_offer("G", "3", dim="volume", amount=1000, brand="Great Value")
    assert names(apply_filters([a, b, g], parse("brand_tier=store_brand"), STORE_BRANDS)) == ["G"]


@pytest.mark.django_db
def test_path_drill_down():
    milk = make_generic_offer("Milk", "3", dim="volume", amount=1000, path="Food/Dairy/Milk")
    cheese = make_generic_offer("Cheese", "3", dim="mass", amount=100, path="Food/Dairy/Cheese")
    off = make_generic_offer("Off", "3", dim="volume", amount=1000, path="Open Food Facts/Dairies")
    offers = [milk, cheese, off]

    def opts(qs):
        return {v: label for v, label, _ in facet_map(offers, qs)["path"].options}

    assert opts("") == {"Food": "Food", "Open Food Facts": "Open Food Facts"}
    assert opts("path=Food") == {"Food/Dairy": "Dairy"}
    assert opts("path=Food/Dairy") == {"Food/Dairy/Milk": "Milk", "Food/Dairy/Cheese": "Cheese"}
    assert names(apply_filters(offers, parse("path=Food"), STORE_BRANDS)) == ["Cheese", "Milk"]
    assert "Open Food Facts/Dairies" not in opts("path=Food")


@pytest.mark.django_db
def test_size_bounds():
    v64 = make_generic_offer("V64", "3", dim="volume", amount=64 * 29.5735295625)
    v128 = make_generic_offer("V128", "3", dim="volume", amount=128 * 29.5735295625)
    mass = make_generic_offer("Mass", "3", dim="mass", amount=1814)
    oz = make_generic_offer("Oz", "3", dim="oz_labeled", amount=64)
    unknown = make_generic_offer("Unk", "3", dim=None, amount=None)
    pool = [v64, v128, mass, oz, unknown]
    assert names(apply_filters(pool, parse("vol_min=60&vol_max=70"), STORE_BRANDS)) == ["V64"]
    for system in ("imperial", "metric"):
        sixteen = make_generic_offer(f"Oz16 {system}", "3", dim="oz_labeled", amount=16)
        kept = apply_filters([sixteen, v64, mass], parse("oz_min=10", system), STORE_BRANDS)
        assert names(kept) == [sixteen.title]
    metric = parse("vol_min=1.8&vol_max=2", "metric")
    assert names(apply_filters(pool, metric, STORE_BRANDS)) == ["V64"]
    assert names(apply_filters(pool, parse("mass_min=1&mass_max=2", "metric"), STORE_BRANDS)) == ["Mass"]


@pytest.mark.django_db
def test_size_facet_range_and_distinct():
    a = make_generic_offer("A", "3", dim="volume", amount=1000)
    assert "vol" not in facet_map([a], system="metric")
    b = make_generic_offer("B", "3", dim="volume", amount=2000)
    f = facet_map([a, b], system="metric")["vol"]
    assert [(v, label) for v, label, _ in f.options] == [("min", "1"), ("max", "2")]


def test_bad_params_dropped():
    f = parse("label=en:made-up&brand_tier=gold&vol_min=abc&vol_max=-3&oz_min=")
    assert f == GenericFilters(frozenset(), None, frozenset(), None, {})
    assert parse("count_min=2&count_max=x").sizes == {"count": (2.0, None)}


@pytest.mark.django_db
def test_repeatable_labels_mean_all():
    both = make_generic_offer("Both", "3", dim="volume", amount=1, labels=["en:organic", "en:vegan"])
    one = make_generic_offer("One", "3", dim="volume", amount=1, labels=["en:organic"])
    kept = apply_filters([both, one], parse("label=en:organic&label=en:vegan"), STORE_BRANDS)
    assert names(kept) == ["Both"]


def test_label_allowlist_file():
    allow = load_label_allowlist(Path(settings.BASE_DIR) / "data" / "labels.yaml")
    assert allow["en:organic"] == "Organic" and allow["en:gluten-free"] == "Gluten-free"
    assert set(allow) >= {"en:vegan", "en:vegetarian"}


@pytest.mark.django_db
def test_generic_never_invents_attribute():
    from catalog.categories_sync import sync_categories

    sync_categories(settings.CATEGORIES_DIR)
    milk = Category.objects.get(slug="dairy/milk")
    organic = raw(title="Organic Gluten Free Oat Milk", item="O1", brand="Oatly")
    conn = FakeConnector({milk.search[0]: SearchResult(offers=[organic], skipped=0)})
    ingest(conn, milk, NOW, off_lookup=lambda upc: None)
    offer = Offer.objects.get(retailer_item_id="O1")
    assert offer.category is None
    assert offer.labels == [] and "organic" not in str(offer.attributes)
    assert apply_filters([offer], parse("label=en:organic"), STORE_BRANDS) == []

    with_off = raw(title="Organic Gluten Free Oat Milk", item="O1", brand="Oatly", upc="012345678905")
    conn = FakeConnector({milk.search[0]: SearchResult(offers=[with_off], skipped=0)})
    ingest(conn, milk, NOW, off_lookup=lambda upc: OffProduct(labels=frozenset({"en:organic"})))
    offer = Offer.objects.get(retailer_item_id="O1")
    assert offer.labels == ["en:organic"]
    assert apply_filters([offer], parse("label=en:organic"), STORE_BRANDS) == [offer]

    gv = raw(title="Great Value Oat Milk", item="G1", brand=None)
    conn = FakeConnector({milk.search[0]: SearchResult(offers=[gv], skipped=0)})
    ingest(conn, milk, NOW, off_lookup=lambda upc: None)
    gv_offer = Offer.objects.get(retailer_item_id="G1")
    assert gv_offer.category is None and "brand_tier" not in gv_offer.attributes
    assert apply_filters([gv_offer], parse("brand_tier=store_brand"), STORE_BRANDS) == []
