from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

pytestmark = pytest.mark.django_db

PUBLIC = "unitpricer.com"
APP = "beta.unitpricer.com"


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.PUBLIC_HOST = PUBLIC
    settings.APP_HOST = APP
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP]
    settings.SECURE_SSL_REDIRECT = False
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@pytest.fixture
def categories(db):
    from django.conf import settings as dj

    from catalog.categories_sync import sync_categories

    sync_categories(dj.CATEGORIES_DIR)


# Every non-admin route the apex serves, incl. M4's lists and account pages and the
# HTMX partial of the category page. test_access.py mirrors this list.
APEX_ROUTES = [
    ("get", "/", {}),
    ("get", "/c/dairy/milk/", {}),
    ("get", "/c/dairy/milk/", {"HTTP_HX_REQUEST": "true"}),
    ("get", "/search?q=green+tea", {}),
    ("get", "/settings", {}),
    ("get", "/disclosure", {}),
    ("get", "/feedback", {}),
    ("get", "/privacy", {}),
    ("get", "/lists/", {}),
    ("get", "/account/sign-in", {}),
]


@pytest.mark.parametrize("method,path,extra", APEX_ROUTES)
def test_apex_app_routes_indexable(client, categories, method, path, extra):
    r = getattr(client, method)(path, HTTP_HOST=PUBLIC, **extra)
    assert r.status_code == 200 and "X-Robots-Tag" not in r


ADMIN_PATHS = ["/admin/", "/admin/login/", "/admin/login/?next=/admin/", "/admin"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
@pytest.mark.parametrize("host", [PUBLIC, PUBLIC + ".", "UnitPricer.COM", "UNITPRICER.COM.", PUBLIC + ":443"])
def test_admin_404s_on_apex(client, settings, path, host):
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP, PUBLIC + "."]
    r = client.get(path, HTTP_HOST=host)
    # A 404, never a redirect to the admin login form.
    assert r.status_code == 404
    assert "X-Robots-Tag" not in r


def test_admin_on_beta(client):
    r = client.get("/admin/login/", HTTP_HOST=APP)
    assert r.status_code == 200
    assert r["X-Robots-Tag"] == "noindex, nofollow"


def test_robots_per_host(client):
    apex = client.get("/robots.txt", HTTP_HOST=PUBLIC).content.decode()
    assert "Allow: /" in apex and "Sitemap: https://unitpricer.com/sitemap.xml" in apex
    for p in ("/search", "/settings", "/lists/", "/account/", "/waitlist/", "/click"):
        assert f"Disallow: {p}" in apex
    assert "Disallow: /\n" not in apex
    assert client.get("/robots.txt", HTTP_HOST=APP).content.decode() == "User-agent: *\nDisallow: /\n"


def test_robots_apex_exact(client):
    body = client.get("/robots.txt", HTTP_HOST=PUBLIC).content.decode()
    assert body == (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /search\n"
        "Disallow: /settings\n"
        "Disallow: /lists/\n"
        "Disallow: /account/\n"
        "Disallow: /waitlist/\n"
        "Disallow: /click\n"
        "\n"
        "Sitemap: https://unitpricer.com/sitemap.xml\n"
    )


@pytest.mark.parametrize("host", [PUBLIC, APP])
def test_cookies_are_host_only(client, host):
    """A cookie set on one host must not be sent to the other: no Domain attribute."""
    from django.conf import settings as dj

    assert dj.SESSION_COOKIE_DOMAIN is None and dj.CSRF_COOKIE_DOMAIN is None
    r = client.get("/account/sign-in", HTTP_HOST=host)
    assert r.status_code == 200
    assert "csrftoken" in r.cookies
    assert r.cookies["csrftoken"]["domain"] == ""


@pytest.mark.parametrize("path", ["/", "/privacy", "/healthz", "/robots.txt"])
def test_apex_serves_public_routes(client, path):
    response = client.get(path, HTTP_HOST=PUBLIC)
    assert response.status_code == 200
    assert "X-Robots-Tag" not in response
    if path == "/robots.txt":
        assert "Allow: /" in response.content.decode()


@pytest.mark.parametrize("path", ["/", "/disclosure"])
def test_app_host_full_app_noindex(client, path):
    response = client.get(path, HTTP_HOST=APP)
    assert response.status_code == 200
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    robots = client.get("/robots.txt", HTTP_HOST=APP)
    assert "Disallow: /" in robots.content.decode()


def test_www_redirects_to_apex(client):
    response = client.get("/privacy?x=1", HTTP_HOST=f"www.{PUBLIC}")
    assert response.status_code == 301
    assert response["Location"] == "https://unitpricer.com/privacy?x=1"


def test_unknown_host_400(client):
    assert client.get("/", HTTP_HOST="evil.example").status_code == 400


def test_no_routing_when_public_host_unset(client, settings):
    settings.PUBLIC_HOST = ""
    settings.ALLOWED_HOSTS = ["testserver"]
    response = client.get("/")
    assert response.status_code == 200
    assert "X-Robots-Tag" not in response


def test_healthz_plain_http_not_redirected(client, settings):
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    response = client.get("/healthz", HTTP_HOST=PUBLIC)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response["Cache-Control"] == "no-store"
    redirect = client.get("/privacy", HTTP_HOST=PUBLIC)
    assert redirect.status_code == 301
    assert redirect["Location"].startswith("https://")


def test_healthz_db_error_503(client, monkeypatch):
    from django.db.backends.utils import CursorWrapper

    def boom(self, *args, **kwargs):
        raise DatabaseError("down")

    monkeypatch.setattr(CursorWrapper, "execute", boom)
    response = client.get("/healthz", HTTP_HOST=PUBLIC)
    assert response.status_code == 503
    assert response.json() == {"status": "db_error"}


def test_client_ip_prefers_cf_header_when_trusted(settings):
    from web.net import client_ip

    request = RequestFactory().get("/", HTTP_CF_CONNECTING_IP=" 203.0.113.7 ", REMOTE_ADDR="172.18.0.2")
    settings.TRUST_CF_IP = True
    assert client_ip(request) == "203.0.113.7"
    settings.TRUST_CF_IP = False
    assert client_ip(request) == "172.18.0.2"


def test_ratelimit_window():
    from web.ratelimit import allow

    window = timedelta(minutes=1)
    assert [allow("t", "a", 3, window) for _ in range(4)] == [True, True, True, False]
    assert allow("t", "b", 3, window) is True


def test_ratelimit_exceeded_peeks_without_counting():
    from web.ratelimit import allow, exceeded

    window = timedelta(minutes=1)
    assert exceeded("p", "a", 2, window) is False
    assert exceeded("p", "a", 2, window) is False  # peeking never counts
    allow("p", "a", 2, window)
    assert exceeded("p", "a", 2, window) is False
    allow("p", "a", 2, window)
    assert exceeded("p", "a", 2, window) is True
    assert exceeded("p", "b", 2, window) is False


@pytest.mark.parametrize("request_host", [PUBLIC, APP])
def test_magic_link_points_at_apex_and_signs_in_there(client, settings, request_host):
    """Production: SITE_URL == PUBLIC_URL, so emailed links land on the apex whatever host asked."""
    import re

    from django.core import mail

    settings.PUBLIC_URL = settings.SITE_URL = "https://unitpricer.com"
    r = client.post("/account/magic-link", {"email": "new@example.com"}, HTTP_HOST=request_host)
    assert r.status_code == 200
    body = mail.outbox[-1].body
    assert "beta." not in body
    path = re.search(r"https://unitpricer\.com(/account/magic/\S+)", body).group(1)
    assert client.get(path, HTTP_HOST=PUBLIC).status_code == 200
    r = client.post(path, HTTP_HOST=PUBLIC)
    assert r.status_code == 302
    session = r.cookies["sessionid"]
    assert session.value and session["domain"] == ""  # host-only: never sent to beta
    assert client.get("/account/", HTTP_HOST=PUBLIC).status_code == 200


def test_waitlist_links_use_apex(client, settings):
    import re

    from django.core import mail

    settings.PUBLIC_URL = "https://unitpricer.com"
    client.post("/waitlist/", {"email": "a@example.com", "website": ""}, HTTP_HOST=APP)
    body = mail.outbox[-1].body
    assert re.search(r"https://unitpricer\.com/waitlist/confirm/\S+", body)
    assert "beta." not in body
