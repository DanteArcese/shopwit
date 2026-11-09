from decimal import Decimal

import pytest

from catalog.ingest import ingest, ingest_live
from catalog.categories_sync import sync_categories
from catalog.models import Category, OffEntry, Offer
from catalog.nutrition import (
    RANKS,
    nutrition_cost,
    nutrition_reason,
    parse_rank,
    refresh_nutrition,
)
from connectors.base import SearchResult
from django.conf import settings
from normalize.types import Nutrition, OffProduct

from tests.catalog.helpers import NOW, FakeConnector, make_offer, raw

UPC = "0012345678905"
POWDER = "Whey Protein Powder Vanilla, 907 g"


def off21(upc):
    return OffProduct(
        labels=frozenset(), quantity=None, nutrition=Nutrition("100g", 21.0, 380.0)
    )


def run_ingest(price, off_lookup, title=POWDER, category="pantry/protein-powder"):
    sync_categories(settings.CATEGORIES_DIR)
    cat = Category.objects.get(slug=category)
    q = cat.search[0]
    conn = FakeConnector({q: SearchResult([raw(title=title, price=price, upc=UPC)], 0)})
    ingest(conn, cat, NOW, off_lookup=off_lookup)
    return Offer.objects.get(retailer="fake", retailer_item_id="A1")


@pytest.mark.django_db
def test_ingest_stores_costs_for_curated_offer():
    offer = run_ingest("2.70", off21)
    assert offer.category.slug == "pantry/protein-powder"
    assert offer.nutrition["protein_cost"] == "1.417546"
    assert offer.nutrition["pack"] == 907


@pytest.mark.django_db
def test_ingest_stores_costs_for_generic_offer():
    offer = run_ingest("2.70", off21, title="Mystery crunchy snack 907 g", category="pantry/rice")
    assert offer.category is None
    assert offer.nutrition["protein_cost"] == "1.417546"
    assert offer.nutrition["kcal_cost"] is not None


@pytest.mark.django_db
def test_ingest_without_off_entry_stores_empty():
    offer = run_ingest("2.70", lambda upc: None)
    assert offer.nutrition == {}


@pytest.mark.django_db
def test_price_change_recomputes_cost():
    assert run_ingest("2.70", off21).nutrition["protein_cost"] == "1.417546"
    assert run_ingest("1.35", off21).nutrition["protein_cost"] == "0.708773"


@pytest.mark.django_db
def test_ingest_live_stores_costs():
    from connectors.live import LiveSearchResult

    sync_categories(settings.CATEGORIES_DIR)
    result = LiveSearchResult(offers=[raw(title=POWDER, price="2.70", upc=UPC)], off_records=[], observations=[], skipped=0)
    ingest_live(result, "fake", NOW, off_lookup=off21)
    assert Offer.objects.get(retailer_item_id="A1").nutrition["protein_cost"] == "1.417546"


def _entry(upc, protein=21.0, kcal=380.0, per="100g"):
    from django.utils import timezone

    return OffEntry.objects.update_or_create(
        upc=upc,
        defaults=dict(
            source="off", nutrition_per=per, protein_100g=protein, kcal_100g=kcal,
            nutrition_checked_at=timezone.now(),
        ),
    )[0]


def _offer_on(upc, item, kind, price="2.70", retailer="walmart"):
    from catalog.models import Product

    product, _ = Product.objects.get_or_create(
        identity=f"p-{upc}", defaults=dict(title="Whey Protein Powder 907 g", upc=upc)
    )
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal(price),
        currency="USD", url="https://example.com/x", title="Whey Protein Powder 907 g",
        last_seen_at=NOW, kind=kind, measure_dim="mass", measure_amount=907.0,
    )


@pytest.mark.django_db
def test_refresh_nutrition_updates_live_and_observed_offers(django_assert_max_num_queries):
    a = _offer_on(UPC, "a", "live")
    b = _offer_on(UPC, "b", "observed", retailer="op-x")
    _entry(UPC)
    with django_assert_max_num_queries(4):
        assert refresh_nutrition([UPC]) == 2
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.nutrition["protein_cost"] == b.nutrition["protein_cost"] == "1.417546"
    assert refresh_nutrition([UPC]) == 0  # nothing changed, nothing written


@pytest.mark.django_db
def test_refresh_nutrition_leaves_other_upcs_alone():
    other = "0099999999991"
    a = _offer_on(UPC, "a", "live")
    c = _offer_on(other, "c", "live")
    _entry(UPC)
    _entry(other, protein=5.0)
    assert refresh_nutrition([UPC]) == 1
    c.refresh_from_db()
    assert c.nutrition == {}
    a.refresh_from_db()
    assert a.nutrition["protein_cost"] == "1.417546"


@pytest.mark.django_db
def test_refresh_nutrition_without_entry_or_unchecked_gives_empty():
    a = _offer_on(UPC, "a", "live")
    a.nutrition = {"protein_cost": "9"}
    a.save()
    assert refresh_nutrition([UPC]) == 1  # no OffEntry: stale cost must go
    a.refresh_from_db()
    assert a.nutrition == {}
    OffEntry.objects.create(upc=UPC, source="off", protein_100g=21.0, nutrition_per="100g")
    assert refresh_nutrition([UPC]) == 0  # never checked: still empty


def test_parse_rank():
    assert [parse_rank(v) for v in ("protein", "kcal", "unit", "PROTEIN", "<x>", None)] == [
        "protein", "kcal", "unit", "unit", "unit", "unit",
    ]
    assert RANKS == {"unit": "Price per unit", "protein": "Protein per $", "kcal": "Calories per $"}


@pytest.mark.django_db
def test_nutrition_cost_and_reason():
    offer = make_offer()
    assert offer.nutrition == {}
    assert nutrition_reason(offer, "protein") == "no nutrition data from Open Food Facts"
    assert nutrition_cost(offer, "protein") is None
    offer.nutrition = {"protein_cost": "1.5", "kcal_cost": None, "protein_reason": None,
                       "kcal_reason": "calories not listed"}
    assert nutrition_cost(offer, "protein") == Decimal("1.5")
    assert nutrition_cost(offer, "kcal") is None
    assert nutrition_reason(offer, "kcal") == "calories not listed"
    assert nutrition_cost(offer, "unit") is None
