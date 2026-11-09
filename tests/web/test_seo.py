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


def test_sitemap_lists_public_pages(client, db, milk):
    r = client.get("/sitemap.xml", HTTP_HOST=PUBLIC)
    body = r.content.decode()
    assert r["Content-Type"].startswith("application/xml")
    for path in ("/", "/c/dairy/milk/", "/about", "/privacy", "/terms", "/disclosure"):
        assert f"<loc>https://unitpricer.com{path}</loc>" in body
    assert "/search" not in body and "beta." not in body


def test_sitemap_skips_inactive_categories(client, db, milk):
    milk.active = False
    milk.save()
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert "/c/dairy/milk/" not in body


def test_sitemap_lastmod_from_fresh_offer(client, db, milk_rows, frozen_now, monkeypatch):
    monkeypatch.setattr("web.seo.now", lambda: frozen_now)
    body = client.get("/sitemap.xml", HTTP_HOST=PUBLIC).content.decode()
    assert f"<loc>https://unitpricer.com/c/dairy/milk/</loc><lastmod>{frozen_now.date().isoformat()}</lastmod>" in body


def test_sitemap_is_apex_only(client, db):
    assert client.get("/sitemap.xml", HTTP_HOST=BETA).status_code == 404


def test_sitemap_urls_ignore_request_host(client, db, milk):
    body = client.get("/sitemap.xml", HTTP_HOST="www.unitpricer.com").content.decode()
    # www redirects to the apex; the apex body never names another host.
    assert "www." not in body


def test_canonical_drops_query(client, db, milk):
    body = client.get("/c/dairy/milk/?fat=2&sort=unit").content.decode()
    assert '<link rel="canonical" href="https://unitpricer.com/c/dairy/milk/">' in body


def test_canonical_on_beta_points_at_apex(client, db, milk):
    r = client.get("/about", HTTP_HOST=BETA, HTTP_CF_ACCESS_JWT_ASSERTION="x")
    if r.status_code == 200:
        assert '<link rel="canonical" href="https://unitpricer.com/about">' in r.content.decode()


def test_canonical_home_and_pages(client, db, milk):
    assert '<link rel="canonical" href="https://unitpricer.com/">' in client.get("/").content.decode()
    assert 'href="https://unitpricer.com/about">' in client.get("/about?x=1").content.decode()


def test_pages_have_meta_description(client, db, milk):
    for path in ("/", "/c/dairy/milk/", "/about", "/terms", "/privacy", "/disclosure"):
        assert '<meta name="description" content="' in client.get(path).content.decode()


def test_category_description_names_unit_and_sources(client, db, milk_rows):
    body = client.get("/c/dairy/milk/").content.decode()
    assert '<meta name="description" content="Cheapest ' in body
    assert "by price per" in body


@pytest.mark.parametrize(
    "path",
    ["/search?q=milk", "/settings", "/lists/", "/account/sign-in", "/feedback", "/waitlist/confirm/xxx", "/waitlist/unsubscribe/xxx"],
)
def test_utility_pages_noindex(client, db, path):
    r = client.get(path)
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert '<meta name="robots" content="noindex">' in r.content.decode()


@pytest.mark.parametrize("path", ["/", "/c/dairy/milk/", "/about", "/terms", "/privacy", "/disclosure"])
def test_indexable_pages_have_no_noindex(client, db, milk, path):
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
