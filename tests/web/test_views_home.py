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
    assert 'href="/c/dairy/milk/"' in html
    assert 'href="/disclosure"' in html
    assert 'aria-label="Search all products"' in html


def test_no_external_asset_urls(client, categories):
    html = client.get("/").content.decode()
    urls = re.findall(r'(?:src|href)="([^"]*)"', html)
    assert urls
    external = [u for u in urls if re.match(r"(?:https?:)?//", u)]
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
