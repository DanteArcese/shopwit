from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Category, ClickEvent, Offer, Product, Store
from catalog.retailers import chain_names, display_name
from tests.catalog.helpers import NOW

OBS_TITLE = "Zephyr Whole Milk, 1 L"


@pytest.fixture
def stores(db):
    def make(code, name, city, chain_code="op-safeway", chain="Safeway"):
        return Store.objects.create(
            code=code, name=name, city=city, chain_code=chain_code, chain_name=chain,
            country_code="US", osm_type="node", osm_id=abs(hash(code)) % 10**6,
        )

    return make


@pytest.fixture
def observe(milk, stores):
    def _obs(price_id=1, store=None, price="0.50", title=OBS_TITLE, sale=False, on=date(2026, 11, 3), n=None):
        store = store or Store.objects.filter(code="osm-n1").first() or stores("osm-n1", "Safeway", "Mountain View")
        product = Product.objects.create(identity=f"obs-{price_id}", title=title, category=milk)
        return Offer.objects.create(
            product=product, retailer=store.chain_code, retailer_item_id=f"{price_id}@{store.code}",
            price=Decimal(price), currency="USD", url=f"https://prices.openfoodfacts.org/prices/{price_id}",
            category=milk, title=title, brand="Zephyr", measures={"volume_ml": 1000.0},
            unit_prices={"per_gal": str(Decimal(price) * Decimal("3.785"))}, last_seen_at=NOW,
            kind="observed", observed_on=on, is_sale=sale, store=store,
        )

    return _obs


def get(client, params=""):
    return client.get(f"/c/dairy/milk/{params}")


def test_observed_section_renders(client, milk_rows, observe):
    observe()
    html = get(client).content.decode()
    assert "Seen in stores" in html
    assert "seen at Safeway, Mountain View on" in html and "Nov 3, 2026" in html
    assert 'href="https://prices.openfoodfacts.org/prices/1"' in html
    assert "Shelf prices people recorded in stores. Not live: prices may have changed since." in html
    assert 'href="https://prices.openfoodfacts.org"' in html
    assert 'href="https://opendatacommons.org/licenses/odbl/1-0/"' in html
    assert "Open Database License (ODbL)" in html


def test_observed_link_has_no_ping_or_affiliate_mark(client, milk_rows, observe):
    observe()
    html = get(client).content.decode()
    section = html[html.index('<section class="observed">'):]
    section = section[: section.index("</section>")]
    link = section[section.index('<a href="https://prices.openfoodfacts.org/prices/1"'):]
    link = link[: link.index(">")]
    assert "ping" not in link and 'rel="noopener noreferrer"' in link
    assert "(affiliate link)" not in section and "data-offer" not in section


def test_live_ranking_excludes_observed(client, milk_rows, observe):
    observe()
    html = get(client).content.decode()
    live = html[: html.index('<section class="observed">')]
    assert OBS_TITLE not in live


def test_search_excludes_observed(client, milk_rows, observe):
    observe(title="Quokka Nectar")
    resp = client.get("/search?q=Quokka")
    assert resp.status_code == 200
    assert "Quokka Nectar" not in resp.content.decode()


def test_store_filter_chain_filters_only_observed(client, milk_rows, observe, stores):
    observe()
    other = stores("osm-n2", "Safeway", "Palo Alto")
    observe(price_id=2, store=other, title="Other Milk")
    stores("osm-n3", "Lucky", "Oakland", chain_code="op-lucky", chain="Lucky")
    html = get(client, "?store=op-safeway").content.decode()
    # live ranking unaffected by a chain-only selection
    assert "Bravo Whole Milk" in html and "Seen in stores" in html
    assert "Safeway (in-store prices)" in html and html.count("Safeway (in-store prices)") == 1
    assert "Safeway, Mountain View" in html and "Safeway, Palo Alto" in html


def test_chain_filter_narrows_observed(client, milk_rows, observe, stores):
    observe()
    lucky = stores("osm-n3", "Lucky", "Oakland", chain_code="op-lucky", chain="Lucky")
    observe(price_id=3, store=lucky, title="Lucky Milk")
    html = get(client, "?store=op-lucky").content.decode()
    assert "Lucky Milk" in html and OBS_TITLE not in html


def test_live_store_selection_keeps_observed_section(client, milk_rows, observe):
    observe()
    html = get(client, "?store=fake").content.decode()
    assert "Seen in stores" in html and OBS_TITLE in html


def test_live_selection_filters_live_only(client, milk_rows, observe):
    observe()
    html = get(client, "?store=walmart").content.decode()
    assert "Bravo Whole Milk" not in html  # fake retailer filtered out
    assert OBS_TITLE in html


def test_chain_prefs_do_not_empty_live_ranking(client, milk_rows, observe):
    observe()
    session = client.session
    session["prefs"] = {"unit_system": "metric", "stores": ["op-safeway"]}
    session.save()
    html = get(client).content.decode()
    assert "Bravo Whole Milk" in html and OBS_TITLE in html


def test_search_ignores_chain_prefs(client, milk_rows, observe):
    session = client.session
    session["prefs"] = {"unit_system": "metric", "stores": ["op-safeway"]}
    session.save()
    html = client.get("/search?q=Mystery+Whole").content.decode()
    assert "Mystery" in html


def test_no_section_without_observed(client, milk_rows):
    html = get(client).content.decode()
    assert "Seen in stores" not in html and "Open Database License" not in html


def test_sale_label(client, milk_rows, observe):
    observe(sale=True)
    assert "sale price" in get(client).content.decode()
    Offer.objects.update(is_sale=False)
    assert "sale price" not in get(client).content.decode()


def test_click_rejects_observed(client, milk_rows, observe):
    o = observe()
    resp = client.post(f"/click?o={o.pk}")
    assert resp.status_code == 404
    assert ClickEvent.objects.count() == 0


def test_chain_names_batched(db, stores):
    stores("osm-n1", "Safeway", "A")
    stores("osm-n2", "Safeway", "B")
    with CaptureQueriesContext(connection) as q:
        names = chain_names(["op-safeway", "walmart", "op-missing"])
    assert len(q) == 1
    assert display_name("op-safeway", names) == "Safeway (in-store prices)"
    assert display_name("op-missing", names) == "op-missing"


def test_category_page_query_count_independent_of_rows(client, milk_rows, observe, stores):
    observe()

    def count():
        with CaptureQueriesContext(connection) as q:
            assert get(client).status_code == 200
        return len(q)

    base = count()
    for i in range(2, 8):
        st = stores(f"osm-n{i * 10}", f"Chain{i}", "Town", chain_code=f"op-chain{i}", chain=f"Chain{i}")
        observe(price_id=i, store=st, title=f"Milk {i}")
    assert count() == base


def test_hx_category_response_includes_observed_section(client, milk_rows, observe):
    observe()
    html = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true").content.decode()
    assert "<html" not in html
    assert "Seen in stores" in html and OBS_TITLE in html
    assert "Open Database License (ODbL)" in html


def test_hx_category_response_omits_empty_observed_section(client, milk_rows):
    html = client.get("/c/dairy/milk/", HTTP_HX_REQUEST="true").content.decode()
    assert "Bravo Whole Milk" in html and "Seen in stores" not in html


def test_settings_page_query_count_independent_of_chains(client, db, stores, observe):
    observe()

    def count():
        with CaptureQueriesContext(connection) as q:
            assert client.get("/settings").status_code == 200
        return len(q)

    base = count()
    for i in range(2, 7):
        st = stores(f"osm-n{i * 10}", f"Chain{i}", "Town", chain_code=f"op-chain{i}", chain=f"Chain{i}")
        observe(price_id=i, store=st, title=f"Milk {i}")
    assert count() == base
    assert "Chain3 (in-store prices)" in client.get("/settings").content.decode()
