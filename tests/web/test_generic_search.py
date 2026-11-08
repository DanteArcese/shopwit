from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Category, Offer, Product, Store
from tests.catalog.helpers import NOW
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
