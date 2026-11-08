from types import SimpleNamespace
from urllib.parse import quote

import pytest
from django.urls import Resolver404, resolve

from catalog.links import link_attrs, outbound_url
from tests.catalog.helpers import raw

CANON = "https://www.walmart.com/ip/milk/123?a=1&b=2#x"


def offer(retailer="walmart", url=CANON, pk=7):
    return SimpleNamespace(retailer=retailer, url=url, pk=pk)


def test_walmart_impact_url_encodes_canonical():
    url = outbound_url(offer(), {"walmart_impact_publisher_id": "PUB1"})
    assert url == f"https://goto.walmart.com/c/PUB1/568844/9383?veh=aff&u={quote(CANON, safe='')}"


def test_sovrn_fallback():
    url = outbound_url(offer("target", "https://target.com/p/1?x=y"), {"sovrn_site_key": "K"})
    assert url == f"https://sovrn.co?key=K&u={quote('https://target.com/p/1?x=y', safe='')}"
    # walmart without an impact id also falls through to sovrn
    assert outbound_url(offer(), {"sovrn_site_key": "K"}).startswith("https://sovrn.co?key=K&u=")


def test_amazon_tag_added_or_replaced():
    aff = {"amazon_associates_tag": "me-20", "sovrn_site_key": "K"}
    added = outbound_url(offer("amazon", "https://www.amazon.com/dp/B0?th=1#frag"), aff)
    assert added == "https://www.amazon.com/dp/B0?th=1&tag=me-20#frag"
    replaced = outbound_url(offer("amazon", "https://www.amazon.com/dp/B0?tag=old-20&th=1"), aff)
    assert replaced == "https://www.amazon.com/dp/B0?tag=me-20&th=1"
    bare = outbound_url(offer("amazon", "https://www.amazon.com/dp/B0"), aff)
    assert bare == "https://www.amazon.com/dp/B0?tag=me-20"


def test_no_config_returns_canonical():
    for aff in ({}, {"walmart_impact_publisher_id": None, "sovrn_site_key": None, "amazon_associates_tag": None}):
        assert outbound_url(offer(), aff) == CANON


def test_sponsored_rel_only_when_affiliate():
    plain = link_attrs(offer(), {})
    assert plain == {"href": CANON, "sponsored": False, "rel": "noopener noreferrer", "target": "_blank", "ping": "/click?o=7"}
    spons = link_attrs(offer(), {"sovrn_site_key": "K"})
    assert spons["rel"] == "sponsored noopener noreferrer"
    assert spons["href"].startswith("https://sovrn.co")
    # amazon with only a walmart id configured: no route, not sponsored
    assert link_attrs(offer("amazon", "https://www.amazon.com/dp/B0"), {"walmart_impact_publisher_id": "P"})["rel"] == "noopener noreferrer"


def test_no_redirect_route_exists():
    with pytest.raises(Resolver404):
        resolve("/go/1")


@pytest.fixture
def amazon_rows(load):
    load([raw(title="Bravo Whole Milk, 1 L", price="1.00", item="b", retailer="amazon")])


@pytest.mark.django_db
def test_disclosure_banner_present_when_links_shown(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "We may earn a commission when you buy through links on this site. It never affects ranking." in html
    assert html.index("We may earn a commission") < html.index('class="results"')
    assert 'href="/disclosure"' in html


@pytest.mark.django_db
def test_no_banner_when_no_links(client):
    html = client.get("/").content.decode()
    assert "We may earn a commission" not in html


@pytest.mark.django_db
def test_amazon_disclaimer_when_amazon_offer(client, amazon_rows, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "As an Amazon Associate I earn from qualifying purchases." in html
    assert "Product prices and availability are accurate as of the date/time indicated and are subject to change." in html


@pytest.mark.django_db
def test_no_amazon_disclaimer_without_amazon_offer(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Amazon Associate" not in html
    assert "subject to change" not in html


@pytest.mark.django_db
def test_rendered_link_uses_affiliate_and_ping(client, milk_rows, settings):
    settings.AFFILIATE = {"sovrn_site_key": "K"}
    html = client.get("/c/dairy/milk/").content.decode()
    assert 'href="https://sovrn.co?key=K&amp;u=' in html
    assert 'rel="sponsored noopener noreferrer"' in html
    assert 'ping="/click?o=' in html


@pytest.mark.django_db
def test_disclosure_page(client):
    html = client.get("/disclosure").content.decode()
    assert "Some product data from Open Food Facts, available under the ODbL." in html
    assert "commission" in html


def test_disclosure_has_open_prices_notice(client):
    html = client.get("/disclosure").content.decode()
    assert 'href="https://prices.openfoodfacts.org"' in html
    assert 'href="https://opendatacommons.org/licenses/odbl/1-0/"' in html
    assert "Open Database License (ODbL)" in html
    assert "available free on request" in html and "hello@unitpricer.com" in html
    assert 'href="https://github.com/DanteArcese/unitpricer"' in html
    assert "not live" in html and "90 days" in html
