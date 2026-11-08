"""Whole-branch review fixes for M3."""

from decimal import Decimal
from pathlib import Path

import pytest

from catalog.browse import FilterSpec, browse
from catalog.models import Offer
from catalog.units import G_PER_OZ, ML_PER_FL_OZ, size_to_canonical
from tests.catalog.helpers import NOW, raw

pytestmark = pytest.mark.usefixtures("frozen_now")
US = {"HTTP_ACCEPT_LANGUAGE": "en-US"}
DE = {"HTTP_ACCEPT_LANGUAGE": "de-DE"}
STATIC = Path(__file__).resolve().parents[2] / "web" / "static" / "web"


# 1. size bounds
def test_constants_match_pint():
    assert ML_PER_FL_OZ == Decimal("29.5735295625")
    assert G_PER_OZ == Decimal("28.349523125")


@pytest.fixture
def big_milk(load):
    load([
        raw(title="Whole Milk, 64 fl oz", price="4.00", item="a64"),
        raw(title="Whole Milk, 1 gal", price="5.00", item="g1"),
    ])


@pytest.mark.parametrize("params", ["size_max=64", "size_min=64&size_max=64", "size_min=64"])
def test_imperial_64_floz_included(client, big_milk, params):
    html = client.get(f"/c/dairy/milk/?{params}", **US).content.decode()
    assert "Whole Milk, 64 fl oz" in html


def test_gallon_at_max_128(client, big_milk):
    html = client.get("/c/dairy/milk/?size_max=128", **US).content.decode()
    assert "Whole Milk, 1 gal" in html and "Whole Milk, 64 fl oz" in html


def test_mass_16_oz_at_max_16(db):
    from catalog.models import Category, Product
    from catalog.categories_sync import sync_categories
    from django.conf import settings

    sync_categories(settings.CATEGORIES_DIR)
    cat = Category.objects.filter(dimension="mass", active=True).first()
    if cat is None:
        pytest.skip("no mass category")
    p = Product.objects.create(identity="m16", title="Sixteen oz", category=cat)
    Offer.objects.create(
        product=p, retailer="fake", retailer_item_id="m16", price="1.00", currency="USD",
        url="https://example.com/m", title="Sixteen oz", last_seen_at=NOW,
        measures={"mass_g": float(Decimal("16") * Decimal("28.349523125"))},
    )
    spec = FilterSpec(attributes={}, size_max=size_to_canonical(Decimal(16), "mass", "imperial"))
    result = browse(cat, spec, None, None, NOW)
    assert result.ranked or result.unranked


# 2. history restore
def test_history_restore_returns_full_page(client, milk_rows):
    resp = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true", HTTP_HX_HISTORY_RESTORE_REQUEST="true")
    assert "<html" in resp.content.decode()


# 3. affiliate marker
def test_affiliate_marker_with_route(client, milk_rows, settings):
    settings.AFFILIATE = {"sovrn_site_key": "K"}
    html = client.get("/c/dairy/milk/").content.decode()
    assert "(affiliate link)" in html


def test_no_marker_for_plain_links(client, milk_rows, settings):
    settings.AFFILIATE = {}
    html = client.get("/c/dairy/milk/").content.decode()
    assert "affiliate link" not in html


# 4. beacon
def test_app_js_click_counting():
    js = (STATIC / "app.js").read_text()
    assert 'removeAttribute("ping")' in js
    assert "navigator.sendBeacon" in js
    assert "preventDefault" not in js
    assert '"ping" in HTMLAnchorElement' not in js


# 5. overflow
def test_size_overflow_ignored(client, milk_rows):
    resp = client.get("/c/dairy/milk/?size_min=1e999999")
    assert resp.status_code == 200
    assert "Bravo Whole Milk" in resp.content.decode()


# 6. empty-state wording
def test_only_unranked_wording(client, load):
    load([raw(title="Mystery Whole Milk", price="3.00", item="d")])
    html = client.get("/c/dairy/milk/").content.decode()
    assert "No fresh prices match these filters" not in html
    assert "None of these can be ranked by price per" in html
    assert 'href="/c/dairy/milk/"' in html


def test_both_empty_wording(client, milk_rows):
    html = client.get("/c/dairy/milk/?fat=skim").content.decode()
    assert "No fresh prices match these filters" in html


# 7. OOB updates
def test_partial_updates_settings_link_and_q_rest(client, milk_rows):
    resp = client.get("/c/dairy/milk/?q_rest=zebra&fat=2", HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert 'id="settings-link"' in html and 'hx-swap-oob="true"' in html
    assert "next=/c/dairy/milk/%3Fq_rest%3Dzebra%26fat%3D2" in html
    assert 'id="q-rest-note"' in html and "zebra" in html


def test_partial_clears_q_rest_note(client, milk_rows):
    html = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true").content.decode()
    assert 'id="q-rest-note"' in html and "also matching" not in html


# 8. size_unit
def test_size_unit_param_overrides_viewer_system(client, big_milk):
    # 64 fl oz bound, viewed by a metric user: must still mean fl oz.
    html = client.get("/c/dairy/milk/?size_max=64&size_unit=floz", **DE).content.decode()
    assert "Whole Milk, 64 fl oz" in html
    assert 'name="size_unit" value="floz"' in html
    html = client.get("/c/dairy/milk/?size_max=64", **DE).content.decode()  # 64 L, includes all
    assert 'name="size_unit" value="l"' in html


def test_size_unit_invalid_falls_back(client, big_milk):
    html = client.get("/c/dairy/milk/?size_max=1&size_unit=bogus", **DE).content.decode()
    assert "Whole Milk, 64 fl oz" not in html


# 9. unranked cap
def test_unranked_capped(milk, frozen_now):
    from catalog.models import Product

    for i in range(7):
        p = Product.objects.create(identity=f"u{i}", title=f"Unsized {i}", category=milk)
        Offer.objects.create(
            product=p, retailer="fake", retailer_item_id=f"u{i}", price="1.00", currency="USD",
            url="https://example.com/u", title=f"Unsized {i}", last_seen_at=NOW,
            unresolved=["quantity"],
        )
    result = browse(milk, FilterSpec(attributes={}), None, None, NOW, limit=3)
    assert len(result.unranked) == 3 and result.unranked_total == 7


def test_unranked_cap_note(client, milk, frozen_now, monkeypatch):
    from catalog.models import Product
    import web.views as views

    orig = views.browse
    monkeypatch.setattr(views, "browse", lambda *a, **k: orig(*a, limit=2, **k))
    for i in range(4):
        p = Product.objects.create(identity=f"u{i}", title=f"Unsized {i}", category=milk)
        Offer.objects.create(
            product=p, retailer="fake", retailer_item_id=f"u{i}", price="1.00", currency="USD",
            url="https://example.com/u", title=f"Unsized {i}", last_seen_at=NOW,
            unresolved=["quantity"],
        )
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Showing 2 of 4 that can" in html


# 10. Vary
def test_vary_accept_language(client, milk_rows, db):
    for url in ("/c/dairy/milk/", "/"):
        vary = client.get(url).headers.get("Vary", "")
        assert "Accept-Language" in vary
    vary = client.get("/c/dairy/milk/").headers["Vary"]
    assert "HX-Request" in vary and "HX-History-Restore-Request" in vary
    assert "Accept-Language" in client.get("/search", {"q": "zzzqqq"}).headers["Vary"]


# 11. UTC fallback
def test_time_fallback_says_utc(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert "Nov 7, 12:00 PM UTC</time>" in html


# 12. unsafe scheme
def test_javascript_url_not_linked(client, milk_rows):
    Offer.objects.update(url="javascript:alert(1)")
    resp = client.get("/c/dairy/milk/")
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "javascript:" not in html
    assert "Bravo Whole Milk" in html


def test_link_attrs_scheme():
    from types import SimpleNamespace
    from catalog.links import link_attrs

    o = SimpleNamespace(pk=1, retailer="fake", url="javascript:alert(1)")
    assert link_attrs(o, {})["href"] is None
    o.url = "https://example.com/x"
    attrs = link_attrs(o, {})
    assert attrs["href"] == o.url and attrs["sponsored"] is False
    assert link_attrs(o, {"sovrn_site_key": "K"})["sponsored"] is True


# 13. live status
def test_live_status_element(client, milk_rows):
    html = client.get("/c/dairy/milk/").content.decode()
    assert '<div id="results" aria-live' not in html
    assert 'id="results-status"' in html and 'role="status"' in html
    assert "4 results" in html
    part = client.get("/c/dairy/milk/?fat=2", HTTP_HX_REQUEST="true").content.decode()
    assert 'id="results-status"' in part and "1 result<" in part


# 15. search banner, reason order
def test_search_banner(client, db):
    from tests.web.test_views_search import _product

    _product("Ginger Snaps Cookies", "3.00")
    assert "We may earn a commission" in client.get("/search", {"q": "ginger"}).content.decode()
    assert "We may earn a commission" not in client.get("/search", {"q": "zzzqqq"}).content.decode()


def test_reason_prefers_quantity_over_attribute(milk, frozen_now):
    from catalog.browse import _reason

    o = Offer(unresolved=["attribute:ply", "quantity"])
    assert _reason(o, "area_x_ply") == "size unknown"
