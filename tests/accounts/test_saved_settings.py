import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from accounts.merge import merge_session_into_account
from accounts.models import UserSettings
from catalog.models import Offer, Product
from tests.catalog.helpers import NOW, raw

User = get_user_model()
PASSWORD = "correct-horse-battery-9"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    monkeypatch.setattr("web.views.now", lambda: NOW)


def make_user(email="u@example.com"):
    return User.objects.create_user(username=email, email=email, password=PASSWORD)


def sign_in(client, email="u@example.com"):
    r = client.post("/account/sign-in", {"email": email, "password": PASSWORD})
    assert r.status_code == 302
    return client


@pytest.fixture
def retailers(db):
    for r in ("walmart", "target"):
        p = Product.objects.create(identity=r, title=r)
        Offer.objects.create(
            product=p, retailer=r, retailer_item_id=r, price="1.00", currency="USD",
            url="https://example.com/x", last_seen_at=NOW,
        )


@pytest.mark.django_db
def test_settings_persist_across_devices(retailers):
    make_user()
    a = sign_in(Client())
    r = a.post(
        "/settings",
        {"unit_system": "metric", "stores": ["walmart"], "postal_code": "94043", "radius": "50"},
    )
    assert r.status_code == 302
    assert "prefs" not in a.session
    row = UserSettings.objects.get()
    assert row.unit_system == "metric" and row.stores == ["walmart"]
    assert row.postal_code == "94043" and row.radius == 50

    b = sign_in(Client(HTTP_ACCEPT_LANGUAGE="en-US"))
    page = b.get("/settings").content.decode()
    assert 'name="unit_system" value="metric" checked' in page
    assert 'value="94043"' in page
    assert re.search(r'name="stores" value="walmart" checked', page)
    assert not re.search(r'name="stores" value="target" checked', page)


import re  # noqa: E402


@pytest.mark.django_db
def test_prefs_merge_account_wins_session_fills_gaps():
    u = make_user()
    # No row: session fills everything.
    merge_session_into_account(
        {"prefs": {"unit_system": "imperial", "stores": ["walmart"], "postal_code": "94043",
                   "country": "US", "radius": 50, "comparisons": {"a": "x", "b": "y"}}},
        u,
    )
    row = UserSettings.objects.get(user=u)
    assert row.unit_system == "imperial" and row.stores == ["walmart"]
    assert row.postal_code == "94043" and row.radius == 50

    # Account set: account wins, comparisons union with account winning.
    row.unit_system = "metric"
    row.stores = ["target"]
    row.comparisons = {"b": "z", "c": "w"}
    row.save()
    merge_session_into_account(
        {"prefs": {"unit_system": "imperial", "stores": ["walmart"], "postal_code": "10001",
                   "radius": 100, "comparisons": {"a": "x", "b": "y"}}},
        u,
    )
    row.refresh_from_db()
    assert row.unit_system == "metric" and row.stores == ["target"]
    assert row.postal_code == "94043" and row.radius == 50
    assert row.comparisons == {"a": "x", "b": "z", "c": "w"}


@pytest.mark.django_db
def test_prefs_merge_blank_account_unit_system_takes_session():
    u = make_user()
    UserSettings.objects.create(user=u, unit_system="", comparisons={"a": "x"})
    merge_session_into_account({"prefs": {"unit_system": "imperial", "stores": None}}, u)
    assert UserSettings.objects.get(user=u).unit_system == "imperial"


@pytest.mark.django_db
def test_prefs_merge_ignores_malformed_session_prefs():
    u = make_user()
    session = {"prefs": {"unit_system": "bogus", "stores": "x", "comparisons": ["no"], "radius": True}}
    merge_session_into_account(session, u)
    assert "prefs" not in session
    row = UserSettings.objects.get(user=u)
    assert row.comparisons == {} and row.radius == 25


@pytest.fixture
def tp_offers(db):
    from django.conf import settings

    from catalog.categories_sync import sync_categories
    from catalog.ingest import ingest
    from catalog.models import Category
    from connectors.base import SearchResult
    from tests.catalog.helpers import FakeConnector, no_off

    sync_categories(settings.CATEGORIES_DIR)
    cat = Category.objects.get(slug="household/toilet-paper")
    offers = [
        raw(title="Acme Toilet Paper 2 ply, 6 rolls", price="6.00", item="a", brand="Acme"),
        raw(title="Bravo Toilet Paper 2 ply, 12 rolls", price="9.00", item="b", brand="Bravo"),
    ]
    conn = FakeConnector({q: SearchResult(offers=offers, skipped=0) for q in cat.search})
    ingest(conn, cat, NOW, off_lookup=no_off)


@pytest.mark.django_db
def test_default_comparison_used_on_category_page(tp_offers):
    u = make_user()
    UserSettings.objects.create(user=u, unit_system="metric", comparisons={"household/toilet-paper": "per_roll"})
    c = sign_in(Client())
    assert c.get("/c/household/toilet-paper/").context["comparison"] == "per_roll"
    over = c.get("/c/household/toilet-paper/?comparison=per_100_sheets")
    assert over.context["comparison"] == "per_100_sheets"


@pytest.mark.django_db
def test_invalid_saved_comparison_falls_back(tp_offers):
    u = make_user()
    UserSettings.objects.create(user=u, unit_system="metric", comparisons={"household/toilet-paper": "per_volume"})
    c = sign_in(Client())
    assert c.get("/c/household/toilet-paper/").context["comparison"] == "area_x_ply"


@pytest.mark.django_db
def test_anonymous_session_comparison_used(tp_offers):
    c = Client()
    session = c.session
    session["prefs"] = {"unit_system": "metric", "stores": None, "comparisons": {"household/toilet-paper": "per_roll"}}
    session.save()
    assert c.get("/c/household/toilet-paper/").context["comparison"] == "per_roll"


@pytest.mark.django_db
def test_settings_page_saves_comparison_and_default_removes(tp_offers):
    c = Client()
    page = c.get("/settings").content.decode()
    assert 'name="cmp:household/toilet-paper"' in page
    c.post("/settings", {"unit_system": "metric", "cmp:household/toilet-paper": "per_roll"})
    assert c.session["prefs"]["comparisons"] == {"household/toilet-paper": "per_roll"}
    c.post("/settings", {"unit_system": "metric", "cmp:household/toilet-paper": ""})
    assert "comparisons" not in c.session["prefs"]


@pytest.mark.django_db
def test_settings_page_drops_unknown_category_and_comparison(tp_offers):
    u = make_user()
    UserSettings.objects.create(user=u, unit_system="metric", comparisons={"gone/cat": "per_x"})
    c = sign_in(Client())
    c.post(
        "/settings",
        {"unit_system": "metric", "cmp:household/toilet-paper": "per_volume", "cmp:nope/nope": "per_roll"},
    )
    assert UserSettings.objects.get(user=u).comparisons == {}
    c.post("/settings", {"unit_system": "metric", "cmp:household/toilet-paper": "per_roll"})
    assert UserSettings.objects.get(user=u).comparisons == {"household/toilet-paper": "per_roll"}


@pytest.mark.django_db
def test_anonymous_settings_still_session_only(retailers):
    c = Client()
    c.post("/settings", {"unit_system": "metric", "stores": ["walmart"]})
    assert c.session["prefs"] == {"unit_system": "metric", "stores": ["walmart"]}
    assert not UserSettings.objects.exists()


@pytest.mark.django_db
def test_postal_code_not_in_url_or_logs(retailers, caplog):
    make_user()
    c = sign_in(Client())
    with caplog.at_level("DEBUG"):
        r = c.post("/settings", {"unit_system": "metric", "postal_code": "94043"})
    assert "94043" not in r["Location"]
    assert "94043" not in caplog.text


@pytest.mark.django_db
def test_privacy_page_lists_saved_settings():
    html = Client().get("/privacy").content.decode()
    assert "ZIP code" in html and "saved settings" in html


@pytest.mark.django_db
def test_deleting_account_deletes_settings():
    u = make_user()
    UserSettings.objects.create(user=u, unit_system="metric")
    u.delete()
    assert not UserSettings.objects.exists()
