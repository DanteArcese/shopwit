"""Category pages ranking by protein or calories per dollar (#95)."""

import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Category, Offer
from normalize.nutrition import offer_nutrition
from normalize.types import Nutrition
from tests.catalog.helpers import NOW, FakeConnector, no_off, raw
from connectors.base import SearchResult
from catalog.ingest import ingest

pytestmark = pytest.mark.usefixtures("frozen_now")

ATTRIBUTION = (
    'Nutrition: <a href="https://world.openfoodfacts.org">Open Food Facts</a>, available under the '
    '<a href="https://opendatacommons.org/licenses/odbl/1-0/">Open Database License (ODbL)</a>.'
)


def nutrify(title, basis="100ml", protein=3.4, kcal=60.0):
    for o in Offer.objects.filter(title__startswith=title):
        o.nutrition = offer_nutrition(
            o.price, Nutrition(basis, protein, kcal), o.measures, o.measure_dim, o.measure_amount
        )
        o.save(update_fields=["nutrition"])


@pytest.fixture
def ranked_milk(milk_rows):
    # Bravo $1.00/L, Cargo $1.50/L, Acme $2.00/L by unit price; protein order is the reverse.
    nutrify("Bravo", protein=2.0)   # 1*10000/(2*1000) = 5.00
    nutrify("Cargo", protein=4.0)   # 1.5*10000/(4*1000) = 3.75
    nutrify("Acme", protein=8.0, kcal=100.0)  # 2.5
    return milk_rows


def test_food_category_shows_rank_select(client, ranked_milk):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Rank by" in html and 'name="rank"' in html
    for label in ("Price per unit", "Protein per $", "Calories per $"):
        assert label in html
    assert re.search(r'<option[^>]*value="unit"[^>]*selected', html)
    html = client.get("/c/dairy/milk/?rank=kcal").content.decode()
    assert re.search(r'<option[^>]*value="kcal"[^>]*selected', html)


def test_rank_orders_rows_and_keeps_unranked(client, ranked_milk):
    html = client.get("/c/dairy/milk/?rank=protein").content.decode()
    assert html.index("Acme Organic") < html.index("Cargo Milk") < html.index("Bravo Whole") < html.index("Mystery Whole")
    assert "no nutrition data from Open Food Facts" in html
    assert "None of these can be ranked by" not in html


def test_none_ranked_message_uses_rank_label(client, milk_rows):
    nutrify("Acme", protein=8.0, kcal=100.0)  # the only cost, filtered out below
    html = client.get("/c/dairy/milk/?rank=kcal&fat=whole").content.decode()
    assert "Acme Organic" not in html
    assert "None of these can be ranked by calories per $" in html
    assert re.search(r'<option[^>]*value="kcal"[^>]*selected', html)  # the select stays


def test_food_category_without_costs_has_no_rank_select(client, milk_rows):
    nutrify("Bravo", basis="serving")  # OFF data, but nothing to rank by
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Rank by" not in html and 'name="rank"' not in html
    ranked = client.get("/c/dairy/milk/?rank=protein").content.decode()
    assert "Rank by" not in ranked and 'name="rank"' not in ranked
    assert "None of these can be ranked by" not in ranked and "Nutrition: " not in ranked
    assert ranked.index("Bravo Whole") < ranked.index("Cargo Milk") < ranked.index("Acme Organic")


def test_rank_select_ignores_the_page_filters(client, milk_rows):
    nutrify("Acme", protein=8.0)
    html = client.get("/c/dairy/milk/?fat=whole&brand_tier=store_brand").content.decode()
    assert "Acme Organic" not in html
    assert "Rank by" in html and 'name="rank"' in html


def test_non_food_category_ignores_rank(client, load, db):
    from django.conf import settings

    tp = Category.objects.get(slug="household/toilet-paper")
    assert not tp.food
    conn = FakeConnector({q: SearchResult(offers=[
        raw(title="Acme Toilet Paper 12 Rolls", price="9.00", item="a"),
        raw(title="Bravo Toilet Paper 12 Rolls", price="6.00", item="b"),
    ], skipped=0) for q in tp.search})
    ingest(conn, tp, NOW, off_lookup=no_off)
    plain = client.get("/c/household/toilet-paper/").content.decode()
    ranked = client.get("/c/household/toilet-paper/?rank=protein").content.decode()
    assert 'name="rank"' not in ranked and "Rank by" not in ranked
    assert "Nutrition: " not in ranked
    assert ranked.index("Bravo") < ranked.index("Acme")
    assert plain.index("Bravo") < plain.index("Acme")


@pytest.mark.parametrize("qs", ["rank=PROTEIN", "rank=%3Cscript%3E", "rank=protein&rank=kcal"])
def test_odd_rank_values(client, ranked_milk, qs):
    resp = client.get(f"/c/dairy/milk/?{qs}")
    html = resp.content.decode()
    assert resp.status_code == 200
    body = re.sub(r'next=[^"]*', "", html)  # the settings link urlencodes the page URL, inertly
    assert "PROTEIN" not in body and "%3Cscript" not in body and "<script>alert" not in body
    if qs.startswith("rank=PROTEIN") or "script" in qs:
        assert html.index("Bravo Whole") < html.index("Cargo Milk") < html.index("Acme Organic")
        assert "Nutrition: " not in html
    else:  # a repeated param: QueryDict.get returns the last value, so kcal wins
        assert "per 1,000 kcal" in html and "per 100 g protein" not in html
        assert re.search(r'<option[^>]*value="kcal"[^>]*selected', html)
        assert "Nutrition: " in html


def test_rank_row_shows_cost_nutrition_and_attribution(client, ranked_milk):
    html = client.get("/c/dairy/milk/?rank=protein").content.decode()
    assert "$2.50 per 100 g protein" in html
    assert "8 g protein per 100 ml · 1 L pack" in html
    assert html.count(ATTRIBUTION) == 1
    kcal = client.get("/c/dairy/milk/?rank=kcal").content.decode()
    # kcal cost = price * 100000 / (kcal * 1000 ml): Bravo 1.666667, Acme 2.00, Cargo 2.50
    assert "$1.67 per 1,000 kcal" in kcal and "$2.00 per 1,000 kcal" in kcal
    assert "$2.50 per 1,000 kcal" in kcal
    assert kcal.index("Bravo Whole") < kcal.index("Acme Organic") < kcal.index("Cargo Milk")
    assert "100 kcal per 100 ml · 1 L pack" in kcal
    assert ATTRIBUTION not in client.get("/c/dairy/milk/").content.decode()


def test_rank_sets_no_cookie_and_no_prefs(client, ranked_milk):
    base = client.get("/c/dairy/milk/")
    resp = client.get("/c/dairy/milk/?rank=protein")
    assert set(resp.cookies) == set(base.cookies)
    assert "rank" not in str(client.session.get("prefs", {}))
    assert "Set-Cookie" not in resp.headers or "rank" not in resp.headers["Set-Cookie"]


def test_rank_htmx_partial_keeps_rank(client, ranked_milk):
    resp = client.get("/c/dairy/milk/?rank=protein&fat=any", HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert "<html" not in html
    assert html.index("Acme Organic") < html.index("Bravo Whole")
    assert "per 100 g protein" in html and ATTRIBUTION in html


def test_filters_survive_rank_switch(client, ranked_milk):
    html = client.get("/c/dairy/milk/?rank=protein&fat=2&organic=true").content.decode()
    assert "Acme Organic" in html and "Bravo Whole" not in html


def test_add_link_never_carries_rank(client, ranked_milk):
    html = client.get("/c/dairy/milk/?rank=protein").content.decode()
    links = re.findall(r'href="([^"]*/lists/[^"]*)"', html)
    assert links and all("rank=" not in link for link in links)


def test_rank_adds_no_queries(client, ranked_milk):
    client.get("/c/dairy/milk/")  # warm any caches
    with CaptureQueriesContext(connection) as plain:
        client.get("/c/dairy/milk/")
    with CaptureQueriesContext(connection) as ranked:
        client.get("/c/dairy/milk/?rank=protein")
    assert len(ranked) == len(plain)
