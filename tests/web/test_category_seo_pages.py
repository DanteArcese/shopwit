import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from catalog.models import Category, Offer, Product, Store
from tests.catalog.helpers import NOW


@pytest.fixture
def rice(milk):
    return Category.objects.get(slug="pantry/rice")


def live(rice, n, retailer, per_gram, last_seen=NOW):
    product = Product.objects.create(identity=f"rice-live-{n}", title=f"Rice {n}", category=rice)
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=f"r{n}", price=Decimal("3.00"),
        currency="USD", url=f"https://example.com/{n}", category=rice, title=f"Rice {n}",
        measures={"mass_g": 1000.0}, unit_prices={"per_mass": per_gram}, last_seen_at=last_seen,
        kind="live",
    )


def observed(rice, n, per_gram, on, city="Mountain View"):
    store = Store.objects.create(
        code=f"osm-{n}", name="Safeway", city=city, chain_code="op-safeway", chain_name="Safeway",
        country_code="US", osm_type="node", osm_id=n,
    )
    product = Product.objects.create(identity=f"rice-obs-{n}", title=f"Seen rice {n}", category=rice)
    return Offer.objects.create(
        product=product, retailer="op-safeway", retailer_item_id=f"o{n}@{store.code}",
        price=Decimal("3.00"), currency="USD", url="https://prices.openfoodfacts.org/prices/1",
        category=rice, title=f"Seen rice {n}", measures={"mass_g": 1000.0},
        unit_prices={"per_mass": per_gram}, last_seen_at=NOW, kind="observed", observed_on=on,
        store=store,
    )


@pytest.fixture
def rice_rows(rice, frozen_now):
    live(rice, 1, "walmart", "0.002")
    live(rice, 2, "target", "0.004", last_seen=NOW - timedelta(hours=13))
    live(rice, 3, "costco", "0.006", last_seen=NOW - timedelta(hours=20))
    observed(rice, 4, "0.003", date(2026, 11, 3))
    return rice


def head_of(html):
    return html[: html.index("</head>")]


def test_title_uses_imperial_default_without_accept_language(client, rice_rows):
    html = client.get("/c/pantry/rice/").content.decode()
    assert "<title>Rice price per ounce: compare stores | UnitPricer</title>" in html
    assert "kg" not in head_of(html)
    assert "Price per oz" in html and "Price per kg" not in html


def test_title_is_metric_for_metric_region(client, rice_rows):
    html = client.get("/c/pantry/rice/", HTTP_ACCEPT_LANGUAGE="de-DE").content.decode()
    assert "<title>Rice price per kg: compare stores | UnitPricer</title>" in html


def test_description_from_real_rows(client, rice_rows):
    html = client.get("/c/pantry/rice/").content.decode()
    assert (
        '<meta name="description" content="4 prices from 4 stores. Lowest seen: $0.0567/oz at '
        'Walmart (Nov 7, 2026). Compared by price per ounce.">'
    ) in html


def test_description_singular(client, rice, frozen_now):
    live(rice, 1, "walmart", "0.002")
    html = client.get("/c/pantry/rice/").content.decode()
    assert 'content="1 price from 1 store. Lowest seen: $0.0567/oz at Walmart (Nov 7, 2026).' in html


def test_description_ignores_filters_and_saved_stores(client, rice_rows):
    html = client.get("/c/pantry/rice/?brand_tier=name_brand").content.decode()
    assert 'content="4 prices from 4 stores.' in html


def test_empty_category_keeps_no_prices_description_and_fallback_title(client, rice, frozen_now):
    html = client.get("/c/pantry/rice/").content.decode()
    assert 'content="No prices yet for Rice' in html
    assert 'class="category-summary"' not in html


def summary_of(html):
    m = re.search(r'<p class="category-summary">(.*?)</p>', html, re.S)
    return " ".join(m.group(1).split()) if m else None


def test_summary_paragraph(client, rice_rows):
    html = client.get("/c/pantry/rice/").content.decode()
    assert summary_of(html) == (
        "4 prices from 4 stores. Lowest seen: $0.0567/oz at Walmart (Nov 7, 2026). "
        "Typical: $0.0992/oz. Latest price seen Nov 7, 2026."
    )
    assert html.index("<h1>") < html.index('class="category-summary"') < html.index('class="filters-drawer"')


def test_summary_singular_and_no_median_under_three_rows(client, rice, frozen_now):
    live(rice, 1, "walmart", "0.002")
    html = client.get("/c/pantry/rice/").content.decode()
    assert summary_of(html) == (
        "1 price from 1 store. Lowest seen: $0.0567/oz at Walmart (Nov 7, 2026). "
        "Latest price seen Nov 7, 2026."
    )


def test_summary_absent_with_filters_or_other_comparison(client, rice_rows):
    for query in ("?brand_tier=name_brand", "?variety=basmati", "?size_min=1", "?store=walmart"):
        html = client.get(f"/c/pantry/rice/{query}").content.decode()
        assert 'class="category-summary"' not in html, query


def test_summary_absent_with_saved_store_pref(client, rice_rows):
    client.post("/settings", {"unit_system": "imperial", "stores": ["walmart"]})
    html = client.get("/c/pantry/rice/").content.decode()
    assert 'class="category-summary"' not in html
    # the description stays the same for every visitor
    assert 'content="4 prices from 4 stores.' in html


def test_summary_counts_every_product_past_the_page_limit(client, rice, frozen_now):
    for n in range(1, 56):
        live(rice, n, "walmart", "0.002")
    html = client.get("/c/pantry/rice/").content.decode()
    assert summary_of(html).startswith("55 prices from 1 store.")
    assert 'content="55 prices from 1 store.' in html


def test_filter_options_have_labels_and_raw_values(client, rice_rows):
    html = client.get("/c/pantry/rice/").content.decode()
    assert '<option value="long_grain">Long grain</option>' in html
    assert '<option value="arborio">Arborio</option>' in html
    assert ">long_grain<" not in html
    html = client.get("/c/pantry/rice/?variety=long_grain").content.decode()
    assert '<option value="long_grain" selected>Long grain</option>' in html
