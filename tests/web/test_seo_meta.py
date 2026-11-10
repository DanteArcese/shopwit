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


def _jsonld(body):
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    return [json.loads(b) for b in blocks]


def _meta(body, prop):
    m = re.search(rf'<meta property="{re.escape(prop)}" content="([^"]*)">', body)
    return m.group(1) if m else None


def test_home_title_and_social_tags(client, db):
    body = client.get("/").content.decode()
    assert "<title>UnitPricer: compare grocery prices per unit</title>" in body
    assert _meta(body, "og:site_name") == "UnitPricer"
    assert _meta(body, "og:type") == "website"
    assert _meta(body, "og:title") == "UnitPricer: compare grocery prices per unit"
    assert _meta(body, "og:url") == "https://unitpricer.com/"
    assert _meta(body, "og:description").startswith("Compare groceries and household goods")
    assert '<meta name="twitter:card" content="summary">' in body


def test_home_has_website_search_action(client, db):
    (site,) = _jsonld(client.get("/").content.decode())
    assert site["@type"] == "WebSite"
    assert site["url"] == "https://unitpricer.com/"
    action = site["potentialAction"]
    assert action["@type"] == "SearchAction"
    assert action["target"]["urlTemplate"] == "https://unitpricer.com/search?q={search_term_string}"
    assert action["query-input"] == "required name=search_term_string"


def test_category_social_tags_and_breadcrumb(client, db, milk_rows):
    body = client.get("/c/dairy/milk/?fat=2").content.decode()
    assert _meta(body, "og:title") == "Milk price per fluid ounce: compare stores | UnitPricer"
    assert _meta(body, "og:url") == "https://unitpricer.com/c/dairy/milk/"
    assert _meta(body, "og:description").startswith("4 prices from 1 store.")
    (crumbs,) = _jsonld(body)
    assert crumbs["@type"] == "BreadcrumbList"
    assert [(i["position"], i["name"], i["item"]) for i in crumbs["itemListElement"]] == [
        (1, "UnitPricer", "https://unitpricer.com/"),
        (2, "Milk", "https://unitpricer.com/c/dairy/milk/"),
    ]


def test_jsonld_escapes_script_breakout():
    from web.seo import jsonld

    out = jsonld({"name": "</script><script>alert(1)</script>"})
    assert "</script>" not in out and "<" not in out
    assert json.loads(out)["name"] == "</script><script>alert(1)</script>"


def test_other_pages_get_default_social_tags(client, db):
    body = client.get("/about").content.decode()
    assert _meta(body, "og:site_name") == "UnitPricer"
    assert _meta(body, "og:url") == "https://unitpricer.com/about"
    assert _jsonld(body) == []


def test_og_url_hidden_wherever_canonical_is(client, db):
    # A page that suppresses the canonical link (e.g. sign-in links carrying a token in the
    # path) must not leak its path through og:url either.
    from django.template import Context, Template
    from django.test import RequestFactory

    req = RequestFactory().get("/account/magic/SECRET-TOKEN")
    html = Template(
        '{% extends "web/base.html" %}{% block canonical %}{% endblock %}'
    ).render(Context({"request": req, "public_origin": "https://unitpricer.com"}))
    head = html.split("</head>")[0]
    assert "SECRET-TOKEN" not in head
    assert 'property="og:url"' not in head
