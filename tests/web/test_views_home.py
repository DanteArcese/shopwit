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
    assert not [u for u in urls if re.match(r"(?:https?:)?//", u)]
    assert urls.index("/static/web/htmx.min.js") < urls.index("/static/web/app.js")
    assert "/static/web/app.js" in urls
    assert "/static/web/app.css" in urls
