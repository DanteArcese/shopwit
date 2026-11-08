import pytest
from datetime import timedelta

from catalog.models import Offer, Product
from tests.catalog.helpers import NOW

pytestmark = pytest.mark.usefixtures("frozen_now")


def test_search_redirects_to_category_with_filters(client, milk):
    resp = client.get("/search", {"q": "organic 2% milk"})
    assert resp.status_code == 302
    assert resp["Location"] == "/c/dairy/milk/?fat=2&organic=true"


def test_search_redirect_includes_leftover(client, milk):
    resp = client.get("/search", {"q": "organic milk zebra"})
    assert resp.status_code == 302
    assert resp["Location"].startswith("/c/dairy/milk/?")
    assert "organic=true" in resp["Location"]
    assert "q_rest=" in resp["Location"]


def test_search_inactive_category_is_unresolved(client, milk):
    milk.active = False
    milk.save()
    resp = client.get("/search", {"q": "milk"})
    assert resp.status_code == 200


def _product(title, price, retailer="fake", seen=NOW):
    p = Product.objects.create(identity=f"id-{title}", title=title)
    Offer.objects.create(
        product=p, retailer=retailer, retailer_item_id=title, price=price, currency="USD",
        url=f"https://example.com/{title.replace(' ', '-')}", title=title, last_seen_at=seen,
    )
    return p


def test_search_fallback_full_text(client, db):
    _product("Ginger Snaps Cookies", "3.00")
    _product("Ginger Beer Bottle", "2.00", seen=NOW - timedelta(days=5))  # stale
    _product("Plain Crackers", "1.00")
    html = client.get("/search", {"q": "ginger"}).content.decode()
    assert "Ginger Snaps Cookies" in html
    assert "Ginger Beer" not in html
    assert "Plain Crackers" not in html
    assert "price as of" in html
    assert "$3.00" in html
    assert 'rel="noopener noreferrer"' in html


def test_search_fallback_no_results(client, db):
    html = client.get("/search", {"q": "zzzqqq"}).content.decode()
    assert "No fresh prices" in html


def test_search_empty_redirects_home(client, db):
    for q in ("", "   "):
        resp = client.get("/search", {"q": q})
        assert resp.status_code == 302 and resp["Location"] == "/"
    assert client.get("/search").status_code == 302
