import pytest

from catalog.models import Offer, Product
from tests.catalog.helpers import NOW

pytestmark = pytest.mark.usefixtures("frozen_now")


@pytest.fixture
def offers(db):
    for r in ("walmart", "target"):
        p = Product.objects.create(identity=r, title=r)
        Offer.objects.create(product=p, retailer=r, retailer_item_id=r, price="1.00", currency="USD",
                             url="https://example.com/x", last_seen_at=NOW)


def test_settings_page_renders(client, offers):
    html = client.get("/settings?next=/c/dairy/milk/").content.decode()
    assert "All stores" in html and "Walmart" in html and "Target" in html
    assert 'name="unit_system"' in html
    assert "csrfmiddlewaretoken" in html
    assert 'value="/c/dairy/milk/"' in html


def test_settings_saves_and_rejects_external_next(client, offers):
    resp = client.post("/settings", {"unit_system": "metric", "stores": ["walmart"], "next": "/c/dairy/milk/"})
    assert resp.status_code == 302 and resp["Location"] == "/c/dairy/milk/"
    assert client.session["prefs"] == {"unit_system": "metric", "stores": ["walmart"]}
    for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)", "evil"):
        resp = client.post("/settings", {"unit_system": "imperial", "next": bad})
        assert resp["Location"] == "/"
    assert client.session["prefs"] == {"unit_system": "imperial", "stores": None}


def test_settings_ignores_unknown_stores_and_systems(client, offers):
    client.post("/settings", {"unit_system": "bogus", "stores": ["nonesuch"]})
    assert client.session["prefs"]["stores"] is None
    assert client.session["prefs"]["unit_system"] in ("imperial", "metric")


def test_settings_csrf_enforced(db):
    from django.test import Client

    c = Client(enforce_csrf_checks=True)
    assert c.post("/settings", {"unit_system": "metric"}).status_code == 403


def test_saved_stale_store_survives_units_only_save(client, offers):
    client.post("/settings", {"unit_system": "imperial", "stores": ["walmart", "costco"]})
    # costco has no fresh offers but is saved; it must be shown and survive
    html = client.get("/settings").content.decode()
    assert "no current prices" in html
    client.post("/settings", {"unit_system": "metric", "stores": ["walmart", "costco"]})
    assert client.session["prefs"] == {"unit_system": "metric", "stores": ["costco", "walmart"]}


def test_all_saved_stores_down_stays_selected(client, offers):
    client.post("/settings", {"unit_system": "imperial", "stores": ["costco"]})
    assert client.session["prefs"]["stores"] == ["costco"]
    client.post("/settings", {"unit_system": "metric", "stores": ["costco"]})
    assert client.session["prefs"] == {"unit_system": "metric", "stores": ["costco"]}


def test_unknown_store_code_rejected(client, offers):
    client.post("/settings", {"unit_system": "metric", "stores": ["walmart", "bogus"]})
    assert client.session["prefs"]["stores"] == ["walmart"]
