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


# Every route the app serves, including the HTMX partial of the category page.
APP_ROUTES = [
    ("get", "/c/dairy/milk/", {}),
    ("get", "/c/dairy/milk/", {"HTTP_HX_REQUEST": "true"}),
    ("get", "/search?q=milk", {}),
    ("get", "/settings", {}),
    ("post", "/settings", {}),
    ("get", "/disclosure", {}),
    ("get", "/admin/", {}),
    ("get", "/admin/login/", {}),
    ("post", "/click", {}),
    ("get", "/feedback", {}),
    ("post", "/feedback", {}),
    ("get", "/lists/", {}),
    ("post", "/lists/new", {}),
    ("get", "/lists/add?category=dairy%2Fmilk", {}),
]


@pytest.mark.parametrize("method,path,extra", APP_ROUTES)
def test_every_app_route_404s_on_apex(client, method, path, extra):
    response = getattr(client, method)(path, HTTP_HOST=PUBLIC, **extra)
    assert response.status_code == 404
    assert "Page not found" in response.content.decode()


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
    assert exceeded("p", "a", 2) is False
    assert exceeded("p", "a", 2) is False  # peeking never counts
    allow("p", "a", 2, window)
    assert exceeded("p", "a", 2) is False
    allow("p", "a", 2, window)
    assert exceeded("p", "a", 2) is True
    assert exceeded("p", "b", 2) is False
