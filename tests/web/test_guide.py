import json
import re

import pytest


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    settings.PUBLIC_URL = "https://unitpricer.com"
    settings.ALLOWED_HOSTS = ["unitpricer.com", "beta.unitpricer.com", "testserver"]
    settings.SECURE_SSL_REDIRECT = False


def _blocks(body):
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)]


def test_guide_page_h1_and_canonical(client, db):
    resp = client.get("/guides/unit-prices")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "<h1>How to read unit price tags</h1>" in body
    assert '<link rel="canonical" href="https://unitpricer.com/guides/unit-prices">' in body


def test_guide_not_noindex(client, db):
    assert "noindex" not in client.get("/guides/unit-prices").content.decode()


def test_guide_in_sitemap(client, db):
    body = client.get("/sitemap.xml", HTTP_HOST="unitpricer.com").content.decode()
    assert "<loc>https://unitpricer.com/guides/unit-prices</loc>" in body


def test_footer_link_on_home(client, db):
    assert 'href="/guides/unit-prices"' in client.get("/").content.decode()


def test_about_links_guide(client, db):
    assert 'href="/guides/unit-prices"' in client.get("/about").content.decode()


def test_guide_jsonld(client, db):
    blocks = _blocks(client.get("/guides/unit-prices").content.decode())
    article = next(b for b in blocks if b["@type"] == "Article")
    assert article["datePublished"] == article["dateModified"] == "2026-11-10"
    assert article["author"] == {"@type": "Person", "name": "Dante Arcese"}
    assert article["publisher"]["name"] == "UnitPricer"
    crumbs = next(b for b in blocks if b["@type"] == "BreadcrumbList")
    assert crumbs["itemListElement"][1]["item"] == "https://unitpricer.com/guides/unit-prices"
