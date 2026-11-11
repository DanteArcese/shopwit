import re

import pytest
from django.conf import settings

from catalog.categories_sync import sync_categories


@pytest.fixture
def categories(db):
    sync_categories(settings.CATEGORIES_DIR)


def test_home_renders_search_and_categories(client, categories):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'action="/search"' in html
    assert 'method="get"' in html
    assert 'name="q"' in html
    assert 'type="search"' in html
    assert 'aria-label="Search products"' in html
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert 'href="/disclosure"' in html
    assert 'aria-label="Search all products"' in html


def test_no_external_asset_urls(client, categories):
    html = client.get("/").content.decode()
    urls = re.findall(r'(?:src|href)="([^"]*)"', html)
    assert urls
    external = [u for u in urls if re.match(r"(?:https?:)?//", u) and u != "http://testserver/"]  # canonical (PUBLIC_URL unset)
    assert external == ["https://github.com/DanteArcese/unitpricer"]  # AGPL source link only
    assert urls.index("/static/web/htmx.min.js") < urls.index("/static/web/app.js")
    assert "/static/web/app.js" in urls
    assert "/static/web/app.css" in urls


def test_home_has_exactly_one_search_form(client, categories):
    html = client.get("/").content.decode()
    assert len(re.findall(r"<form\b[^>]*role=\"search\"", html)) == 1
    assert 'class="search search-large"' in html
    assert 'class="site-header no-search"' in html


def test_other_pages_keep_header_search(client, categories):
    for url in ("/c/dairy/milk/", "/search?q=mystery"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        html = resp.content.decode()
        header = re.search(r"<header.*?</header>", html, re.S)
        assert header and 'role="search"' in header.group(0), url
        assert len(re.findall(r"<form\b[^>]*role=\"search\"", html)) == 1, url


# --- Task 5: honest coverage -------------------------------------------------

from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

from catalog.models import Category  # noqa: E402
from tests.catalog.helpers import make_observed_offer  # noqa: E402


def _save_zip(client, postal="94043"):
    session = client.session
    session["prefs"] = {"unit_system": "imperial", "stores": None, "postal_code": postal}
    session.save()


def test_home_lists_coverage_and_zip_prompt(client, categories, frozen_now):
    make_observed_offer(chain="Market Basket")
    body = client.get("/").content.decode()
    assert "Stores we cover" in body and "Market Basket (1 store)" in body
    assert "Seen in stores near you" in body and "Set ZIP code" in body
    assert "Open Database License (ODbL)" in body


def test_home_nearby_categories_with_zip(client, categories, frozen_now):
    milk = Category.objects.get(slug="dairy/milk")
    make_observed_offer(category=milk)
    _save_zip(client)
    body = client.get("/").content.decode()
    nearby = body.split("Seen in stores near you")[1].split("Browse categories")[0]
    assert 'href="/c/dairy/milk/"' in nearby and "1 price<" in nearby
    assert "Set ZIP code" not in nearby


def test_home_nearby_empty_with_zip(client, categories, frozen_now):
    _save_zip(client)
    body = client.get("/").content.decode()
    assert "No recorded in-store prices within" in body


def test_home_says_when_no_live_prices(client, categories, frozen_now, settings):
    settings.COMING_SOON = "Walmart (December)"
    body = client.get("/").content.decode()
    assert "No live online prices yet — coming: Walmart (December)." in body
    settings.COMING_SOON = ""
    body = client.get("/").content.decode()
    assert "No live online prices yet." in body and "coming:" not in body


def test_home_names_live_retailers(client, categories, frozen_now):
    from tests.catalog.helpers import make_offer

    make_offer(retailer="walmart")
    body = client.get("/").content.decode()
    assert "Online, live prices: Walmart." in body and "No live online prices" not in body


def test_home_coverage_sets_no_cookies_and_bounded_queries(client, categories, frozen_now):
    make_observed_offer(chain="Market Basket")
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get("/")
    assert not resp.cookies
    assert len(ctx) <= 6
    for i in range(5):
        make_observed_offer(chain=f"Chain {i}", store_code=f"c{i}")
    with CaptureQueriesContext(connection) as more:
        client.get("/")
    assert len(more) == len(ctx)


def _nearby_section(body):
    return body.split("Seen in stores near you")[1].split("Browse categories")[0]


def test_home_unknown_zip_copy_is_about_nearby(client, categories, frozen_now):
    milk = Category.objects.get(slug="dairy/milk")
    make_observed_offer(category=milk)
    _save_zip(client, postal="00000")
    nearby = _nearby_section(client.get("/").content.decode())
    assert "We don't have a location for ZIP code 00000, so we can't show stores near you." in nearby
    assert "all stores are shown" not in nearby
    assert "Change ZIP code" in nearby
    assert 'href="/c/dairy/milk/"' not in nearby


def test_home_ignores_unplaced_stores(client, categories, frozen_now):
    milk = Category.objects.get(slug="dairy/milk")
    make_observed_offer(category=milk, store_code="nowhere", lat=None, lon=None)
    _save_zip(client)
    nearby = _nearby_section(client.get("/").content.decode())
    assert 'href="/c/dairy/milk/"' not in nearby
    assert "No recorded in-store prices within" in nearby
