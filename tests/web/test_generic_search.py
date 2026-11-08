from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Offer, Product, Store
from tests.catalog.test_generic_match import make_generic_offer

pytestmark = pytest.mark.usefixtures("frozen_now")


def get(client, q, **extra):
    return client.get("/search", {"q": q}, **extra)


def html(client, q, **extra):
    resp = get(client, q, **extra)
    assert resp.status_code == 200
    return resp.content.decode()


def set_prefs(client, **prefs):
    session = client.session
    session["prefs"] = {"unit_system": "imperial", "stores": None, **prefs}
    session.save()


def test_measure_grouping(client, db):
    make_generic_offer("Fizz Soda A", "2", dim="volume", amount=1000)
    make_generic_offer("Fizz Soda B", "3", dim="volume", amount=1000)
    make_generic_offer("Fizz Soda Powder", "4", dim="mass", amount=500)
    make_generic_offer("Fizz Soda Cans", "5", dim="count", amount=12)
    combo = make_generic_offer("Fizz Soda Combo 16.9 fl oz (500 g)", "6", dim=None, amount=None)
    Offer.objects.filter(pk=combo.pk).update(measure_reason="more than one kind of size")
    set_prefs(client, unit_system="metric")
    page = html(client, "fizz soda")
    order = [page.index(t) for t in ("By volume (2)", "By weight (1)", "By count (1)", "Size unknown (1)")]
    assert order == sorted(order)
    assert "more than one kind of size" in page
    volume = page[order[0] : order[1]]
    weight = page[order[1] : order[2]]
    count = page[order[2] : order[3]]
    assert "/ L" in volume and "/ kg" not in volume and "/ each" not in volume
    assert "/ kg" in weight and "/ L" not in weight
    assert "/ each" in count and "/ L" not in count


@pytest.mark.parametrize("system", ["imperial", "metric"])
def test_bare_oz_own_group(client, db, system):
    make_generic_offer("Great Value 2% Milk, 64 oz", "3.20", dim="oz_labeled", amount=64)
    make_generic_offer("Barilla Spaghetti, 16 oz", "1.60", dim="oz_labeled", amount=16)
    set_prefs(client, unit_system=system)
    page = html(client, "oz")
    assert "Per oz as labeled (2)" in page
    assert 'Sold by "oz" without saying weight or fluid ounces, so these are compared only with each other.' in page
    assert "/ oz (as labeled)" in page
    assert "By volume" not in page and "By weight" not in page


@pytest.mark.parametrize("system, label", [("metric", "/ L"), ("imperial", "/ fl oz")])
def test_unit_display_unified(client, milk, load, system, label):
    from catalog.units import display_unit_price, format_money
    from tests.catalog.helpers import raw

    load([raw(title="Bravo Whole Milk, 1 L", price="1.00", item="b", brand="Bravo")])
    set_prefs(client, unit_system=system)
    unit = Offer.objects.get().unit_prices["per_volume"]
    price, _label = display_unit_price(Decimal(unit), "per_volume", system)
    expected = f"{format_money(price, 'USD')} {label}"
    assert expected in client.get("/c/dairy/milk/").content.decode()
    make_generic_offer("Bravo Oat Drink", "1.00", dim="volume", amount=1000)
    assert expected in html(client, "bravo oat drink")


def test_curated_query_still_redirects(client, milk):
    resp = get(client, "2% milk")
    assert resp.status_code == 302 and resp["Location"] == "/c/dairy/milk/?fat=2"


def test_near_miss_links(client, milk):
    for i in range(2):
        product = Product.objects.create(identity=f"oat{i}", title=f"Oat Milk {i}", category=milk)
        make_generic_offer(f"Oat Milk {i}", "3", dim="volume", amount=1000, product=product)
    page = html(client, "oat milk")
    assert "Milk: more filters" in page
    assert 'href="/c/dairy/milk/?q_rest=oat+milk"' in page


def test_near_miss_typo_and_cap(client, db):
    from django.conf import settings

    from catalog.categories_sync import sync_categories

    sync_categories(settings.CATEGORIES_DIR)
    page = html(client, "tolet paper")
    assert "Toilet Paper: more filters" in page
    assert 'href="/c/household/toilet-paper/?q_rest=tolet+paper"' in page
    assert page.count("more filters") <= 3


def test_curated_row_links_category(client, milk):
    product = Product.objects.create(identity="m1", title="Oat Milk Barista", category=milk)
    make_generic_offer("Oat Milk Barista", "3", dim="volume", amount=1000, product=product)
    assert "In Milk" in html(client, "oat milk barista")


def test_seen_in_stores_generic(client, db):
    near = Store.objects.create(
        code="osm-n1", name="Near", city="T", chain_code="op-safeway", chain_name="Safeway",
        country_code="US", osm_type="node", osm_id=1, lat=37.4031206, lon=-122.079306,
    )
    far = Store.objects.create(
        code="osm-n2", name="Far", city="T", chain_code="op-safeway", chain_name="Safeway",
        country_code="US", osm_type="node", osm_id=2, lat=38.5816, lon=-121.4944,
    )
    for store, title in ((near, "Shelf Quinoa Near"), (far, "Shelf Quinoa Far")):
        offer = make_generic_offer(
            title, "2", dim="mass", amount=500, retailer=store.chain_code, kind="observed"
        )
        Offer.objects.filter(pk=offer.pk).update(
            store=store, observed_on=date(2026, 11, 3), url="https://prices.openfoodfacts.org/p/1"
        )
    page = html(client, "shelf quinoa")
    assert "Seen in stores" in page and "Open Database License" in page
    assert "Shelf Quinoa Near" in page and "Shelf Quinoa Far" in page
    live_part = page[: page.index("Seen in stores")]
    assert "Shelf Quinoa" not in live_part
    set_prefs(client, postal_code="94043", country="US", radius=10)
    page = html(client, "shelf quinoa")
    assert "Shelf Quinoa Near" in page and "Shelf Quinoa Far" not in page
    assert "1 more from stores farther away" in page


def test_live_rows_have_report_and_outbound(client, db):
    offer = make_generic_offer("Zesty Relish Jar", "2", dim="mass", amount=300)
    page = html(client, "zesty relish")
    assert f"offer={offer.pk}" in page and "Report a problem" in page
    assert 'rel="noopener noreferrer"' in page or "sponsored" in page
    assert "We may earn a commission" in page


def test_generic_freshness(client, db):
    make_generic_offer("Stale Pretzel", "2", dim="mass", amount=300, retailer="walmart", age_hours=25)
    page = html(client, "stale pretzel")
    assert "Stale Pretzel" not in page and "No saved prices match" in page


def test_show_all_group(client, db):
    for i in range(30):
        make_generic_offer(f"Bulk Rice {i:02d}", str(2 + i), dim="mass", amount=1000)
    page = html(client, "bulk rice")
    assert page.count('class="result"') == 25 and "Show all 30" in page
    resp = client.get("/search", {"q": "bulk rice", "show": "mass"}, HTTP_HX_REQUEST="true")
    body = resp.content.decode()
    assert body.count('class="result"') == 30
    assert "<html" not in body and "Show all" not in body and "Seen in stores" not in body


def test_search_sets_no_cookies(client, db):
    make_generic_offer("Cookie Free Tea", "2", dim="mass", amount=100)
    assert not get(client, "cookie free tea").cookies


def test_htmx_swap_is_partial_with_vary(client, db):
    make_generic_offer("Partial Tea", "2", dim="mass", amount=100)
    resp = client.get("/search", {"q": "partial tea"}, HTTP_HX_REQUEST="true")
    body = resp.content.decode()
    assert "<html" not in body and 'hx-swap-oob' in body
    assert {"HX-Request", "HX-History-Restore-Request", "Accept-Language"} <= set(
        v.strip() for v in resp["Vary"].split(",")
    )


def test_apex_has_no_search(client, db, settings):
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.ALLOWED_HOSTS = ["unitpricer.com"]
    assert client.get("/search?q=x", HTTP_HOST="unitpricer.com").status_code == 404
    assert client.get("/search?q=x&show=mass", HTTP_HOST="unitpricer.com", HTTP_HX_REQUEST="true").status_code == 404


@pytest.mark.parametrize("q", ["the", "!!!", '"milk', "-", "a", "🥛", "x" * 200])
def test_degenerate_queries_render(client, db, q):
    assert "No saved prices match" in html(client, q)


def test_query_is_escaped(client, db):
    page = html(client, '"><script>alert(1)</script>')
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_query_with_quotes_cannot_break_attributes(client, milk):
    nasty = 'bulk rice" onmouseover="x\'><b>'
    for i in range(30):
        make_generic_offer(f"Bulk Rice onmouseover x {i:02d}", str(2 + i), dim="mass", amount=1000)
    for i in range(2):
        product = Product.objects.create(identity=f"rice{i}", title=f"Bulk Rice onmouseover x Milk {i}", category=milk)
        make_generic_offer(f"Bulk Rice onmouseover x Milk {i}", "3", dim="volume", amount=1000, product=product)
    page = html(client, nasty)
    assert 'hx-get="?q=bulk%20rice%22%20onmouseover%3D%22x%27%3E%3Cb%3E&amp;show=mass' in page
    assert 'href="/c/dairy/milk/?q_rest=bulk+rice%22+onmouseover%3D%22x%27%3E%3Cb%3E"' in page
    assert 'onmouseover="x' not in page and "<b>" not in page


def test_generic_view_query_count(client, db):
    def count(n):
        Offer.objects.all().delete()
        Product.objects.all().delete()
        for i in range(n):
            make_generic_offer(f"Countable Pasta {i}", "2", dim="mass", amount=500 + i)
        with CaptureQueriesContext(connection) as ctx:
            assert get(client, "countable pasta").status_code == 200
        return len(ctx)

    few, many = count(6), count(60)
    assert few == many and few <= 15


# ---- Task 7: facets --------------------------------------------------------------------


def results_titles(page):
    return sorted(t for t in ("Oat Drink Oatly", "Oat Drink Alpro", "Oat Drink Plain") if t in page)


@pytest.fixture
def oat_drinks(db):
    make_generic_offer("Oat Drink Oatly", "4", dim="volume", amount=1000, brand="Oatly", labels=["en:organic"], path="Food/Dairy/Milk")
    make_generic_offer("Oat Drink Alpro", "3", dim="volume", amount=2000, brand="Alpro", path="Food/Dairy/Milk")
    make_generic_offer("Oat Drink Plain", "2", dim="volume", amount=1500, path="Food/Dairy/Cheese")


def test_facets_render_and_filter(client, oat_drinks):
    set_prefs(client, unit_system="metric")
    page = html(client, "oat drink")
    assert 'id="facets"' in page and 'name="brand"' in page and 'value="oatly"' in page
    assert "Organic (from Open Food Facts)" in page
    assert results_titles(page) == ["Oat Drink Alpro", "Oat Drink Oatly", "Oat Drink Plain"]
    resp = client.get("/search", {"q": "oat drink", "brand": "oatly"})
    page = resp.content.decode()
    assert results_titles(page) == ["Oat Drink Oatly"]
    assert 'value="oatly" checked' in page or 'value="oatly"  checked' in page
    assert "Clear" in page


def test_facets_htmx_swaps_out_of_band(client, oat_drinks):
    resp = client.get("/search", {"q": "oat drink", "brand": "alpro"}, HTTP_HX_REQUEST="true")
    body = resp.content.decode()
    assert "<html" not in body
    assert 'id="facets" hx-swap-oob="true"' in body
    assert "Oat Drink Alpro" in body and "Oat Drink Oatly" not in body


def test_facet_links_keep_state(client, oat_drinks):
    resp = client.get("/search", {"q": "oat drink", "path": "Food", "brand": "oatly"})
    page = resp.content.decode()
    assert "path=Food%2FDairy" in page and "brand=oatly" in page


def test_bad_facet_params_ignored(client, oat_drinks):
    base = html(client, "oat drink")
    resp = client.get(
        "/search", {"q": "oat drink", "label": "en:made-up", "brand_tier": "gold", "vol_min": "abc"}
    )
    assert resp.status_code == 200
    assert results_titles(resp.content.decode()) == results_titles(base)


def test_filter_to_nothing_offers_clear(client, oat_drinks):
    page = client.get("/search", {"q": "oat drink", "brand": "nobody"}).content.decode()
    assert "No products match these filters" in page and "Oat Drink Oatly" not in page


def test_show_all_keeps_filters(client, db):
    for i in range(30):
        make_generic_offer(f"Bulk Rice {i:02d}", str(2 + i), dim="mass", amount=1000, brand="Kernel")
    make_generic_offer("Bulk Rice Other", "2", dim="mass", amount=1000, brand="Other")
    page = client.get("/search", {"q": "bulk rice", "brand": "kernel"}).content.decode()
    assert "Show all 30" in page and "brand=kernel" in page.split("Show all")[0].rsplit("<a", 1)[1]


def seen_offer(title, store_code="osm-n1"):
    store, _ = Store.objects.get_or_create(
        code=store_code, defaults={
            "name": "Near", "city": "T", "chain_code": "op-safeway", "chain_name": "Safeway",
            "country_code": "US", "osm_type": "node", "osm_id": 1, "lat": 37.4031206, "lon": -122.079306,
        },
    )
    offer = make_generic_offer(title, "2", dim="mass", amount=500, retailer="op-safeway", kind="observed")
    Offer.objects.filter(pk=offer.pk).update(
        store=store, observed_on=date(2026, 11, 3), url="https://prices.openfoodfacts.org/p/1"
    )


def test_status_counts_in_store_results(client, db):
    make_generic_offer("Mixed Quinoa Live", "2", dim="mass", amount=500)
    seen_offer("Mixed Quinoa Shelf")
    page = html(client, "mixed quinoa")
    assert 'role="status" aria-live="polite">2 products' in page


def test_only_in_store_results_neutral_live_line(client, db):
    seen_offer("Shelf Only Farro")
    page = html(client, "shelf only farro")
    status = page[page.index('id="results-status"') :].split("</p>")[0]
    assert "0 products" not in status and "1 product" in status
    assert "No live store prices match" in page and "No saved prices match" not in page


@pytest.mark.parametrize("bound", ["9e999999", "1e400", "1e999999999999"])
def test_huge_size_bounds_ignored(client, oat_drinks, bound):
    base = results_titles(html(client, "oat drink"))
    for param in ("vol_min", "vol_max", "mass_min", "oz_max", "count_min"):
        resp = client.get("/search", {"q": "oat drink", param: bound})
        assert resp.status_code == 200
        assert results_titles(resp.content.decode()) == base


def _hidden_path(body):
    import re

    m = re.search(r'<input type="hidden" name="path" value="([^"]*)"', body)
    return m.group(1) if m else None


def test_path_survives_htmx_updates(client, oat_drinks):
    hx = {"HTTP_HX_REQUEST": "true"}
    full = client.get("/search", {"q": "oat drink"}).content.decode()
    assert _hidden_path(full) is None
    # set path via an hx request, then toggle a brand as the form would (hidden path included)
    body = client.get("/search", {"q": "oat drink", "path": "Food/Dairy"}, **hx).content.decode()
    assert _hidden_path(body) == "Food/Dairy"
    body = client.get(
        "/search", {"q": "oat drink", "path": _hidden_path(body), "brand": "oatly"}, **hx
    ).content.decode()
    assert _hidden_path(body) == "Food/Dairy"
    # clear path (the clear link drops it); the next toggle must not bring it back
    body = client.get("/search", {"q": "oat drink", "brand": "oatly"}, **hx).content.decode()
    assert _hidden_path(body) is None
    body = client.get(
        "/search", {"q": "oat drink", "brand": ["oatly", "alpro"]}, **hx
    ).content.decode()
    assert _hidden_path(body) is None


@pytest.mark.parametrize("q", ["\x00", "milk\x00", "\x01\x1f", "mi\x00lk\x7f"])
def test_control_characters_never_error(client, db, q):
    resp = get(client, q)
    assert resp.status_code in (200, 302)
    if resp.status_code == 200:
        page = resp.content.decode()
        assert "\x00" not in page and "No saved prices match" in page


def test_nul_query_url_encoded(client, db):
    assert client.get("/search?q=%00").status_code == 302  # nothing left: home
    resp = client.get("/search?q=milk%00")
    assert resp.status_code == 200 and "\x00" not in resp.content.decode()


def test_fuzzy_and_capped_notes_are_visible(client, db):
    make_generic_offer("Chobani Greek Yogurt", "2", dim="mass", amount=500)
    page = html(client, "chobanni")
    notes = page[page.index('class="search-notes"'):]
    assert "Showing close matches for &#x27;chobanni&#x27;" in notes[: notes.index("</div>")]
