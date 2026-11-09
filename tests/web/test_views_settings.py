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
    assert resp.status_code == 302
    assert resp["Location"] == "/settings?saved=1&next=%2Fc%2Fdairy%2Fmilk%2F"
    assert client.session["prefs"] == {"unit_system": "metric", "stores": ["walmart"]}
    for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)", "evil"):
        resp = client.post("/settings", {"unit_system": "imperial", "next": bad})
        assert resp["Location"] == "/settings?saved=1"
    assert client.session["prefs"] == {"unit_system": "imperial", "stores": ["walmart"]}


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


def _saved(client, stores):
    client.post("/settings", {"unit_system": "imperial", "stores": stores})


def test_units_only_post_preserves_saved_stores(client, offers):
    _saved(client, ["walmart", "costco"])
    client.post("/settings", {"unit_system": "metric"})
    assert client.session["prefs"] == {"unit_system": "metric", "stores": ["costco", "walmart"]}


def test_only_invalid_codes_preserve_saved_stores(client, offers):
    _saved(client, ["walmart"])
    client.post("/settings", {"unit_system": "metric", "stores": ["bogus"]})
    assert client.session["prefs"]["stores"] == ["walmart"]


def test_explicit_all_stores_clears_selection(client, offers):
    _saved(client, ["walmart"])
    client.post("/settings", {"unit_system": "metric", "all_stores": "1", "stores": ["walmart"]})
    assert client.session["prefs"]["stores"] is None


def test_stale_saved_store_rendered_checked(client, offers):
    import re

    _saved(client, ["walmart", "costco"])
    html = client.get("/settings").content.decode()
    assert re.search(r'<input[^>]*name="stores"[^>]*value="costco"[^>]*checked', html)
    assert not re.search(r'<input[^>]*name="all_stores"[^>]*checked', html)


def test_settings_saved_page_confirms_and_links_back(client, offers):
    html = client.get("/settings?saved=1&next=%2Fc%2Fdairy%2Fmilk%2F").content.decode()
    assert 'role="status">Settings saved.' in html
    assert '<a href="/c/dairy/milk/">Back to the page you were on</a>' in html


def test_settings_without_saved_has_no_confirmation(client, offers):
    assert "Settings saved." not in client.get("/settings").content.decode()


@pytest.mark.parametrize("bad", ["https://evil.example/", "//evil.example/x", "javascript:alert(1)", "evil"])
def test_settings_unsafe_next_has_no_back_link(client, offers, bad):
    from urllib.parse import quote

    html = client.get(f"/settings?saved=1&next={quote(bad, safe='')}").content.decode()
    assert "Settings saved." in html
    assert "Back to the page" not in html
    assert "href=\"https://evil" not in html and "href=\"//evil" not in html


def test_settings_next_account_is_link_not_redirect(client, offers):
    resp = client.post("/settings", {"unit_system": "metric", "next": "/account/"})
    assert resp.status_code == 302 and resp["Location"] == "/settings?saved=1&next=%2Faccount%2F"
    html = client.get(resp["Location"]).content.decode()
    assert '<a href="/account/">Back to the page you were on</a>' in html


def test_settings_next_settings_has_no_back_link(client, offers):
    html = client.get("/settings?saved=1&next=%2Fsettings").content.decode()
    assert "Back to the page" not in html
