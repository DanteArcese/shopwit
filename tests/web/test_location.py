"""Saved postal code + radius limiting "Seen in stores" to nearby stores (#63)."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.views.debug import SafeExceptionReporterFilter

from catalog.browse import FilterSpec, browse
from catalog.geo import Near, locate
from catalog.models import Offer, Product, Store
from tests.catalog.helpers import NOW
from web import views
from web.prefs import (
    DEFAULT_RADIUS,
    Prefs,
    get_prefs,
    radius_choices,
    radius_km,
    radius_label,
)

pytestmark = pytest.mark.usefixtures("frozen_now")

# Distances from the ZCTA 94043 centroid (37.411913, -122.068919).
NEAR = (37.4031206, -122.079306)  # Mountain View, ~1.3 km
MID = (37.3382, -121.8863)  # San Jose, ~18 km: inside 25 mi, outside 10 mi
FAR = (38.5816, -121.4944)  # Sacramento, ~140 km


@pytest.fixture
def store(db):
    def make(code, coords, chain="op-safeway"):
        lat, lon = coords if coords else (None, None)
        return Store.objects.create(
            code=code,
            name=f"Store {code}",
            city="Town",
            chain_code=chain,
            chain_name="Safeway",
            country_code="US",
            osm_type="node",
            osm_id=int(code.removeprefix("osm-n")),
            lat=lat,
            lon=lon,
        )

    return make


@pytest.fixture
def observe(milk):
    def _obs(store, title, price="0.50", n=None, volume=1000.0):
        n = n or title
        product = Product.objects.filter(
            identity=f"obs-{title}"
        ).first() or Product.objects.create(
            identity=f"obs-{title}", title=title, category=milk
        )
        return Offer.objects.create(
            product=product,
            retailer=store.chain_code if store else "op-gone",
            retailer_item_id=f"{n}@{store.code if store else 'none'}",
            price=Decimal(price),
            currency="USD",
            url="https://prices.openfoodfacts.org/prices/1",
            category=milk,
            title=title,
            brand="Zephyr",
            measures={"volume_ml": volume},
            unit_prices={"per_gal": str(Decimal(price) * Decimal("3.785"))},
            last_seen_at=NOW,
            kind="observed",
            observed_on=date(2026, 11, 3),
            store=store,
        )

    return _obs


@pytest.fixture
def spread(store, observe):
    observe(store("osm-n1", NEAR), "Near Milk")
    observe(store("osm-n2", MID), "Mid Milk")
    observe(store("osm-n3", FAR), "Far Milk")
    observe(store("osm-n4", None), "Unplaced Milk")
    observe(None, "Storeless Milk")


def set_prefs(client, **prefs):
    session = client.session
    session["prefs"] = {"unit_system": "imperial", "stores": None, **prefs}
    session.save()


def page(client):
    return client.get("/c/dairy/milk/").content.decode()


def section(html):
    return html[html.index('<section class="observed">') :]


# --- prefs and radius conversion -------------------------------------------


def test_radius_conversion():
    assert radius_km(25, "imperial") == pytest.approx(40.2336)
    assert radius_km(100, "imperial") == pytest.approx(160.9344)
    assert radius_km(25, "metric") == 40.0
    assert radius_label(25, "imperial") == "25 mi"
    assert radius_label(25, "metric") == "40 km"
    assert [label for _v, label in radius_choices("imperial")] == [
        "10 mi",
        "25 mi",
        "50 mi",
        "100 mi",
    ]
    assert [label for _v, label in radius_choices("metric")] == [
        "16 km",
        "40 km",
        "80 km",
        "160 km",
    ]
    assert DEFAULT_RADIUS == 25


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({}, (None, "US", 25)),
        ({"postal_code": "94043", "country": "US", "radius": 50}, ("94043", "US", 50)),
        ({"postal_code": "bogus", "radius": 7}, (None, "US", 25)),
        ({"postal_code": "94043", "country": "CA"}, (None, "US", 25)),
        ({"postal_code": "94043", "radius": True}, ("94043", "US", 25)),
    ],
)
def test_get_prefs_reads_location(rf, stored, expected):
    request = rf.get("/")
    request.session = {"prefs": {"unit_system": "imperial", "stores": None, **stored}}
    p = get_prefs(request)
    assert (p.postal_code, p.country, p.radius) == expected


# --- filtering -------------------------------------------------------------


def test_no_postal_code_shows_all_with_prompt(client, milk_rows, spread):
    s = section(page(client))
    for title in (
        "Near Milk",
        "Mid Milk",
        "Far Milk",
        "Unplaced Milk",
        "Storeless Milk",
    ):
        assert title in s
    assert "Set your ZIP code to see stores near you." in s
    assert "farther away" not in s


def test_postal_code_hides_far_and_unplaced_stores(client, milk_rows, spread):
    set_prefs(client, postal_code="94043", country="US")
    s = section(page(client))
    assert "Near Milk" in s and "Mid Milk" in s
    for title in ("Far Milk", "Unplaced Milk", "Storeless Milk"):
        assert title not in s
    assert "1 more from stores farther away" in s
    assert "2 more from stores with unknown location" in s
    assert "Near 94043 · 25 mi · " in s and ">change</a>" in s
    assert "Set your ZIP code" not in s


def test_smaller_radius_hides_more(client, milk_rows, spread):
    set_prefs(client, postal_code="94043", country="US", radius=10)
    s = section(page(client))
    assert "Near Milk" in s and "Mid Milk" not in s
    assert "2 more from stores farther away" in s
    assert "2 more from stores with unknown location" in s
    assert "Near 94043 · 10 mi" in s


def test_metric_viewer_sees_km(client, milk_rows, spread):
    set_prefs(client, unit_system="metric", postal_code="94043", country="US")
    s = section(page(client))
    assert "Near 94043 · 40 km" in s
    assert "Mid Milk" in s


def test_product_with_a_near_store_is_not_counted_hidden(
    client, milk_rows, store, observe
):
    near, far = store("osm-n1", NEAR), store("osm-n3", FAR)
    observe(near, "Shared Milk", price="0.60", n="a")
    observe(far, "Shared Milk", price="0.40", n="b")  # cheaper, but too far
    set_prefs(client, postal_code="94043", country="US")
    s = section(page(client))
    assert "Shared Milk" in s and "$0.60" in s and "$0.40" not in s
    assert "farther away" not in s


def test_hidden_count_respects_size_filter(milk, store, observe, frozen_now):
    observe(store("osm-n1", NEAR), "Near Milk")
    observe(store("osm-n3", FAR), "Far Big Milk", volume=4000.0)
    observe(store("osm-n5", FAR), "Far Small Milk", volume=500.0)
    near = Near(*locate("94043"), radius_km(25, "imperial"))
    spec = FilterSpec(attributes={}, size_min=900.0, size_max=5000.0)
    result = browse(milk, spec, None, None, NOW, observed=True, near=near)
    assert [r.product.title for r in result.ranked + result.unranked] == ["Near Milk"]
    assert result.hidden_far == 1


def test_everything_far_keeps_section_with_empty_note(client, milk_rows, spread):
    set_prefs(client, postal_code="10001", country="US")
    s = section(page(client))
    assert "No in-store prices within 25 mi of 10001." in s
    assert "3 more from stores farther away" in s
    assert "2 more from stores with unknown location" in s
    assert "No in-store prices from the selected stores." not in page(client)


def test_product_far_and_unplaced_counts_as_far(milk, store, observe, frozen_now):
    observe(store("osm-n3", FAR), "Mixed Milk", n="a")
    observe(store("osm-n4", None), "Mixed Milk", n="b")
    observe(None, "Mixed Milk", n="c")
    observe(store("osm-n6", None), "Lost Milk")
    near = Near(*locate("94043"), radius_km(25, "imperial"))
    result = browse(milk, FilterSpec(attributes={}), None, None, NOW, observed=True, near=near)
    assert result.total_products == 0
    assert (result.hidden_far, result.hidden_unplaced) == (1, 1)


def test_only_unplaced_hidden_makes_no_distance_claim(client, milk_rows, store, observe):
    observe(store("osm-n4", None), "Unplaced Milk")
    observe(None, "Storeless Milk")
    set_prefs(client, postal_code="94043", country="US")
    s = section(page(client))
    assert "2 more from stores with unknown location" in s
    assert "within" not in s and "farther away" not in s
    assert "No in-store prices from stores with a known location." in s


def test_unknown_postal_code_filters_nothing_and_says_so(client, milk_rows, spread):
    set_prefs(client, postal_code="00000", country="US")
    s = section(page(client))
    assert "We don't have a location for ZIP code 00000" in s
    for title in ("Near Milk", "Far Milk", "Unplaced Milk", "Storeless Milk"):
        assert title in s
    assert "farther away" not in s and "unknown location" not in s


def test_live_offers_never_filtered_by_distance(client, milk_rows, spread):
    set_prefs(client, postal_code="10001", country="US", radius=10)
    html = page(client)
    live = html[: html.index('<section class="observed">')]
    for title in ("Bravo Whole Milk", "Acme Organic 2% Milk", "Cargo Milk"):
        assert title in live


def test_browse_ignores_near_for_live(milk, milk_rows):
    near = Near(40.75, -73.99, 1.0)
    spec = FilterSpec(attributes={})
    assert browse(milk, spec, None, None, NOW, near=near) == browse(
        milk, spec, None, None, NOW
    )


def test_htmx_response_keeps_location_control(client, milk_rows, spread):
    set_prefs(client, postal_code="94043", country="US")
    html = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true").content.decode()
    assert "Near 94043 · 25 mi" in html


def test_query_count_flat_with_postal_code(client, milk_rows, store, observe):
    set_prefs(client, postal_code="94043", country="US")
    observe(store("osm-n1", NEAR), "Milk 1")

    def count():
        with CaptureQueriesContext(connection) as q:
            assert client.get("/c/dairy/milk/").status_code == 200
        return len(q)

    base = count()
    for i in range(2, 10):
        coords = (NEAR, FAR, None)[i % 3]
        observe(store(f"osm-n{i + 10}", coords, chain=f"op-chain{i}"), f"Milk {i}")
    assert count() == base


def test_postal_code_never_in_page_urls(client, milk_rows, spread):
    set_prefs(client, postal_code="94043", country="US")
    html = page(client)
    assert "postal_code=" not in html and "?94043" not in html and "=94043" not in html


# --- settings --------------------------------------------------------------


def test_settings_form_has_labelled_location_fields(client, db):
    html = client.get("/settings", HTTP_ACCEPT_LANGUAGE="en-US").content.decode()
    assert '<label for="postal-code">ZIP code (US)</label>' in html
    assert 'id="postal-code" name="postal_code"' in html
    assert '<label for="radius">Distance</label>' in html
    assert '<option value="25" selected>25 mi</option>' in html
    for v in ("10", "50", "100"):
        assert f'<option value="{v}">{v} mi</option>' in html


def test_settings_form_metric_choices(client, db):
    set_prefs(client, unit_system="metric")
    html = client.get("/settings").content.decode()
    assert '<option value="25" selected>40 km</option>' in html
    assert '<option value="100">160 km</option>' in html


def test_settings_post_saves_normalized_postal_code(client, db):
    resp = client.post(
        "/settings",
        {"postal_code": " 94043-1351 ", "radius": "50", "next": "/c/dairy/milk/"},
    )
    assert resp.status_code == 302 and resp["Location"] == "/c/dairy/milk/"
    prefs = client.session["prefs"]
    assert (prefs["postal_code"], prefs["country"], prefs["radius"]) == (
        "94043",
        "US",
        50,
    )


def test_settings_post_rejects_malformed_postal_code(client, db):
    set_prefs(client, postal_code="94043", country="US", radius=50)
    resp = client.post(
        "/settings", {"postal_code": "9404x", "radius": "10", "unit_system": "metric"}
    )
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Enter a 5-digit US ZIP code. Your other changes were saved." in html
    assert 'aria-invalid="true"' in html
    prefs = client.session["prefs"]
    assert (prefs["postal_code"], prefs["radius"], prefs["unit_system"]) == ("94043", 10, "metric")
    # The form shows what is saved, not the rejected input.
    assert 'name="postal_code" value="94043"' in html and "9404x" not in html
    assert '<option value="10" selected>16 km</option>' in html
    assert 'value="metric" checked' in html


def test_settings_post_blank_clears_and_absent_keeps(client, db):
    set_prefs(client, postal_code="94043", country="US", radius=50)
    client.post("/settings", {"unit_system": "metric"})
    assert client.session["prefs"]["postal_code"] == "94043"
    assert client.session["prefs"]["radius"] == 50
    client.post("/settings", {"postal_code": "", "radius": "25"})
    assert "postal_code" not in client.session["prefs"]
    assert "radius" not in client.session["prefs"]


@pytest.mark.parametrize("radius", ["7", "abc", "", "25.5"])
def test_settings_post_invalid_radius_keeps_previous(client, db, radius):
    set_prefs(client, postal_code="94043", country="US", radius=50)
    client.post("/settings", {"postal_code": "94043", "radius": radius})
    assert client.session["prefs"]["radius"] == 50


def test_settings_shows_unknown_postal_code_message(client, db):
    client.post("/settings", {"postal_code": "00000"})
    html = client.get("/settings").content.decode()
    assert "We don't have a location for ZIP code 00000" in html


def test_settings_post_is_redacted_from_error_reports(db, settings):
    settings.DEBUG = False
    request = RequestFactory().post("/settings", {"postal_code": "94043"})
    SessionMiddleware(lambda r: None).process_request(request)
    views.settings_page(request)
    cleansed = SafeExceptionReporterFilter().get_post_parameters(request)
    assert cleansed["postal_code"] != "94043"


def test_settings_post_does_not_log_postal_code(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/settings", {"postal_code": "94043"})
    client.post("/settings", {"postal_code": "9404x"})
    set_prefs(client, postal_code="94043", country="US")
    client.get("/c/dairy/milk/")
    assert "94043" not in caplog.text and "9404x" not in caplog.text


def test_prefs_dataclass_defaults():
    p = Prefs("imperial")
    assert (p.postal_code, p.country, p.radius) == (None, "US", 25)


# --- privacy -------------------------------------------------------------


def test_privacy_page_mentions_postal_code(client):
    html = client.get("/privacy").content.decode()
    collect = html[html.index(">What we collect<") : html.index(">Why<")]
    assert (
        "Beta app: the ZIP code and distance you choose in Settings. They are saved on our server "
        "in your session record (linked to your browser by a session cookie, expiring 2 weeks after "
        "you last save your settings), used only to show in-store prices from nearby stores, and "
        "never logged. Clear the ZIP code in Settings to remove it."
    ) in collect


def test_privacy_session_lifetime_matches_settings(settings):
    assert settings.SESSION_COOKIE_AGE == 14 * 24 * 3600
    assert settings.SESSION_SAVE_EVERY_REQUEST is False  # so expiry counts from the last save


def test_settings_page_location_hint(client, db):
    html = client.get("/settings").content.decode()
    assert "Saved with your session on our server; never logged." in html
