import pytest

PUBLIC = "unitpricer.com"
BETA = "beta.unitpricer.com"


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.PUBLIC_HOST = PUBLIC
    settings.APP_HOST = BETA
    settings.PUBLIC_URL = "https://unitpricer.com"
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", BETA, "testserver"]
    settings.SECURE_SSL_REDIRECT = False


def test_sitemap_lists_public_pages(client, db, milk_rows, monkeypatch, frozen_now):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    r = client.get("/sitemap.xml", HTTP_HOST=PUBLIC)
    body = r.content.decode()
    assert r["Content-Type"].startswith("application/xml")
    for path in ("/", "/c/dairy/milk/", "/about", "/privacy", "/terms", "/disclosure"):
        assert f"<loc>https://unitpricer.com{path}</loc>" in body
    assert "/search" not in body and "beta." not in body


def test_sitemap_skips_inactive_categories(client, db, milk, milk_rows, monkeypatch, frozen_now):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    milk.active = False
    milk.save()
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert "/c/dairy/milk/" not in body


def test_sitemap_lastmod_from_fresh_offer(client, db, milk_rows, frozen_now, monkeypatch):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert f"<loc>https://unitpricer.com/c/dairy/milk/</loc><lastmod>{frozen_now.date().isoformat()}</lastmod>" in body


@pytest.mark.parametrize("host", [BETA, "testserver"])
def test_sitemap_is_apex_only(client, db, host):
    assert client.get("/sitemap.xml", HTTP_HOST=PUBLIC).status_code == 200
    assert client.get("/sitemap.xml", HTTP_HOST=host).status_code == 404


def test_sitemap_urls_come_from_public_url_not_request_host(client, db, milk_rows, settings, monkeypatch, frozen_now):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    # PUBLIC_URL deliberately differs from the request host: every <loc> must use it.
    settings.PUBLIC_URL = "https://canonical.example"
    r = client.get("/sitemap.xml", HTTP_HOST=PUBLIC)
    assert r.status_code == 200
    body = r.content.decode()
    assert "<loc>https://canonical.example/c/dairy/milk/</loc>" in body
    assert "<loc>https://canonical.example/about</loc>" in body
    assert "://unitpricer.com" not in body and "testserver" not in body


def test_public_origin_falls_back_to_public_host(client, db, milk, settings):
    settings.PUBLIC_URL = ""
    r = client.get("/sitemap.xml", HTTP_HOST=PUBLIC)
    assert "<loc>https://unitpricer.com/about</loc>" in r.content.decode()
    body = client.get("/about", HTTP_HOST=BETA).content.decode()
    assert '<link rel="canonical" href="https://unitpricer.com/about">' in body


def test_public_origin_uses_request_only_without_public_host(rf, settings):
    from web.seo import public_origin

    settings.PUBLIC_URL = ""
    settings.PUBLIC_HOST = ""
    assert public_origin(rf.get("/", HTTP_HOST="testserver")) == "http://testserver"


def test_canonical_drops_query(client, db, milk):
    body = client.get("/c/dairy/milk/?fat=2&sort=unit").content.decode()
    assert '<link rel="canonical" href="https://unitpricer.com/c/dairy/milk/">' in body


def test_canonical_on_beta_points_at_apex(client, db, milk):
    r = client.get("/about", HTTP_HOST=BETA)
    assert r.status_code == 200
    assert '<link rel="canonical" href="https://unitpricer.com/about">' in r.content.decode()


def test_canonical_home_and_pages(client, db, milk):
    assert '<link rel="canonical" href="https://unitpricer.com/">' in client.get("/").content.decode()
    assert 'href="https://unitpricer.com/about">' in client.get("/about?x=1").content.decode()


def test_pages_have_meta_description(client, db, milk):
    for path in ("/", "/c/dairy/milk/", "/about", "/terms", "/privacy", "/disclosure"):
        assert '<meta name="description" content="' in client.get(path).content.decode()


def test_category_description_counts_prices_and_names_unit(client, db, milk_rows):
    body = client.get("/c/dairy/milk/").content.decode()
    assert '<meta name="description" content="4 prices from 1 store. Lowest seen: $0.0296/fl oz at Fake (Nov 7, 2026).' in body
    assert "Compared by price per fluid ounce." in body


@pytest.mark.parametrize(
    "path",
    ["/search?q=milk", "/settings", "/lists/", "/account/sign-in", "/feedback", "/waitlist/confirm/xxx"],
)
def test_utility_pages_noindex(client, db, path):
    r = client.get(path)
    assert r.status_code == 200
    assert '<meta name="robots" content="noindex">' in r.content.decode()


def test_token_pages_noindex(client, db):
    from waitlist.models import Subscriber
    from waitlist.tokens import confirm_token, unsubscribe_token

    sub = Subscriber.objects.create(email="p@example.com")
    for path, status in [
        (f"/waitlist/confirm/{confirm_token(sub)}", 200),
        (f"/waitlist/unsubscribe/{unsubscribe_token(sub)}", 200),
        ("/waitlist/unsubscribe/xxx", 404),
    ]:
        r = client.get(path)
        assert r.status_code == status, path
        assert '<meta name="robots" content="noindex">' in r.content.decode(), path


@pytest.mark.parametrize("path", ["/", "/c/dairy/milk/", "/about", "/terms", "/privacy", "/disclosure"])
def test_indexable_pages_have_no_noindex(client, db, milk_rows, path):
    assert 'name="robots"' not in client.get(path).content.decode()


@pytest.mark.parametrize(
    "path",
    ["/", "/c/dairy/milk/", "/search?q=milk", "/about", "/terms", "/privacy", "/disclosure", "/sitemap.xml", "/robots.txt"],
)
def test_public_pages_set_no_cookies(client, db, milk, path):
    assert not client.get(path, HTTP_HOST=PUBLIC).cookies


def test_noindex_pages_omit_canonical(client, db):
    # Token URLs must not be echoed into a canonical link.
    body = client.get("/waitlist/confirm/secrettoken").content.decode()
    assert 'rel="canonical"' not in body


def _observed(category, on):
    from catalog.models import Offer, Product

    product = Product.objects.create(identity="obs-seo", title="Zephyr Whole Milk, 1 L", category=category)
    return Offer.objects.create(
        product=product, retailer="op-safeway", retailer_item_id="1@osm-n1", price="0.50",
        currency="USD", url="https://prices.openfoodfacts.org/prices/1", category=category,
        title=product.title, measures={"volume_ml": 1000.0}, last_seen_at=on,
        kind="observed", observed_on=on.date(),
    )


def test_sitemap_leaves_out_categories_without_prices(client, db, milk, monkeypatch, frozen_now):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert "/c/dairy/" not in body and "/c/beverages/soda/" not in body
    assert "<loc>https://unitpricer.com/about</loc>" in body


def test_sitemap_lists_category_with_only_observed_prices(client, db, milk, monkeypatch, frozen_now):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    _observed(milk, frozen_now)
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert f"<loc>https://unitpricer.com/c/dairy/milk/</loc><lastmod>{frozen_now.date().isoformat()}</lastmod>" in body


def test_empty_category_is_noindex_with_honest_description(client, db, milk, frozen_now):
    body = client.get("/c/dairy/milk/").content.decode()
    assert '<meta name="robots" content="noindex">' in body
    assert '<meta name="description" content="No prices yet for Milk' in body
    assert "Cheapest" not in body[: body.index("</head>")]


def test_category_with_observed_prices_is_indexable(client, db, milk, frozen_now):
    _observed(milk, frozen_now)
    body = client.get("/c/dairy/milk/").content.decode()
    assert 'name="robots"' not in body
    assert '<meta name="description" content="1 price from 1 store.' in body


def test_category_with_only_stale_offers_is_noindex(client, db, milk_rows, frozen_now, monkeypatch):
    from datetime import timedelta

    later = frozen_now + timedelta(days=400)
    monkeypatch.setattr("web.views.now", lambda: later)
    body = client.get("/c/dairy/milk/").content.decode()
    assert '<meta name="robots" content="noindex">' in body


def _browse(body):
    return body.split("Browse categories")[1].split("</section>")[0]


def test_home_browse_lists_only_categories_with_fresh_prices(client, db, milk, frozen_now):
    assert "/c/dairy/milk/" not in _browse(client.get("/").content.decode())
    # Its own page still answers, as noindex, while it has no prices.
    page = client.get("/c/dairy/milk/")
    assert page.status_code == 200
    assert '<meta name="robots" content="noindex">' in page.content.decode()

    _observed(milk, frozen_now)
    browse = _browse(client.get("/").content.decode())
    assert 'href="/c/dairy/milk/"' in browse
    assert "/c/beverages/soda/" not in browse


def test_home_browse_drops_category_whose_prices_went_stale(client, db, milk, frozen_now, monkeypatch):
    from datetime import timedelta

    _observed(milk, frozen_now)
    monkeypatch.setattr("web.views.now", lambda: frozen_now + timedelta(days=400))
    assert "/c/dairy/milk/" not in _browse(client.get("/").content.decode())
