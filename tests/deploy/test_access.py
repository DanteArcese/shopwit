import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import web.access

from tests.deploy.routes import APEX_ROUTES, APP, BETA_ROUTES, PUBLIC
TEAM = "unitpricer.cloudflareaccess.com"
AUD = "test-aud"
KEEP_ACCESS_MIDDLEWARE = True


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StubJWKS:
    def __init__(self, public_key, error=None):
        self.public_key = public_key
        self.error = error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return type("Key", (), {"key": self.public_key})()


@pytest.fixture(autouse=True)
def access(settings, monkeypatch, private_key):
    settings.PUBLIC_HOST = PUBLIC
    settings.APP_HOST = APP
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP]
    settings.SECURE_SSL_REDIRECT = False
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM
    settings.CF_ACCESS_AUD = AUD
    monkeypatch.setattr(web.access, "_jwks_client", StubJWKS(private_key.public_key()))


@pytest.fixture
def categories(db):
    from django.conf import settings as dj

    from catalog.categories_sync import sync_categories

    sync_categories(dj.CATEGORIES_DIR)


def make_token(private_key, **overrides):
    claims = {
        "aud": [AUD],
        "iss": f"https://{TEAM}",
        "email": "me@example.com",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def get(client, token=None, path="/", host=APP):
    extra = {"HTTP_CF_ACCESS_JWT_ASSERTION": token} if token else {}
    return client.get(path, HTTP_HOST=host, **extra)


@pytest.mark.django_db
def test_valid_token_passes(client, private_key):
    response = get(client, make_token(private_key))
    assert response.status_code == 200
    assert response.wsgi_request.access_email == "me@example.com"


def test_missing_token_403(client):
    response = get(client)
    assert response.status_code == 403
    assert response.content == b"Access required"


def test_wrong_audience_403(client, private_key):
    assert get(client, make_token(private_key, aud=["other"])).status_code == 403


def test_expired_token_403(client, private_key):
    assert get(client, make_token(private_key, exp=int(time.time()) - 10)).status_code == 403


def test_wrong_issuer_403(client, private_key):
    assert get(client, make_token(private_key, iss="https://evil.example.com")).status_code == 403


def test_garbage_token_403(client):
    assert get(client, "not-a-jwt").status_code == 403


def test_jwks_fetch_failure_fails_closed(client, private_key, monkeypatch):
    err = jwt.PyJWKClientConnectionError("down")
    monkeypatch.setattr(web.access, "_jwks_client", StubJWKS(None, error=err))
    assert get(client, make_token(private_key)).status_code == 403


def test_token_signed_by_other_key_403(client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert get(client, make_token(other)).status_code == 403


@pytest.mark.django_db
def test_apex_and_healthz_not_checked(client):
    assert get(client, host=PUBLIC).status_code == 200
    assert get(client, path="/healthz").status_code == 200


@pytest.mark.django_db
def test_healthz_via_tunnel_is_checked(client):
    r = client.get("/healthz", HTTP_HOST=APP, HTTP_X_FORWARDED_PROTO="https")
    assert r.status_code == 403


@pytest.mark.django_db
def test_inactive_without_aud_in_dev(client, settings):
    settings.CF_ACCESS_AUD = ""
    settings.DEBUG = True
    assert get(client).status_code == 200
    settings.DEBUG = False
    settings.APP_HOST = ""
    assert get(client).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("host", [APP, "localhost"])
@pytest.mark.parametrize("path", ["/", "/admin/", "/lists/"])
def test_missing_aud_fails_closed_in_production(client, settings, host, path):
    # DEBUG off and APP_HOST set but CF_ACCESS_AUD empty: refuse rather than open beta.
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP, "localhost"]
    settings.CF_ACCESS_AUD = ""
    r = client.get(path, HTTP_HOST=host)
    assert r.status_code == 403
    assert r.content == b"Access required"


@pytest.mark.django_db
def test_missing_aud_keeps_apex_and_probe_open(client, settings):
    settings.CF_ACCESS_AUD = ""
    assert get(client, host=PUBLIC).status_code == 200
    assert get(client, path="/healthz").status_code == 200


def test_trailing_dot_host_still_checked(client):
    assert get(client, host=APP + ".").status_code == 403


def test_token_without_exp_403(client, private_key):
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "email": "me@example.com"}
    token = jwt.encode(claims, private_key, algorithm="RS256")
    assert get(client, token).status_code == 403


@pytest.mark.django_db
def test_trailing_dot_public_hosts(client, settings, categories):
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP]
    # The apex is the public app (no token), but the admin never reaches it.
    assert get(client, path="/c/dairy/milk/", host=PUBLIC + ".").status_code == 200
    assert get(client, path="/admin/", host=PUBLIC + ".").status_code == 404
    assert get(client, path="/admin/login/", host=PUBLIC.upper()).status_code == 404
    assert get(client, host=PUBLIC + ".").status_code == 200
    r = get(client, host=f"www.{PUBLIC}.")
    assert r.status_code == 301


@pytest.mark.django_db
def test_healthz_last_ingestion_ignores_open_prices(client):
    from catalog.models import IngestionRun

    live = IngestionRun.objects.create(connector="walmart", category_slug="x", started_at=time_now())
    IngestionRun.objects.create(connector="openprices", category_slug="all", started_at=time_now())
    IngestionRun.objects.create(connector="off-product", category_slug="all", started_at=time_now())
    body = get(client, path="/healthz").json()
    assert body["last_ingestion"] == live.created_at.isoformat()


def time_now():
    from django.utils import timezone

    return timezone.now()


BETA_HOSTS = [APP, APP + ".", APP.upper()]


@pytest.mark.django_db
@pytest.mark.parametrize("host", BETA_HOSTS)
@pytest.mark.parametrize("method,path,extra", BETA_ROUTES)
@pytest.mark.parametrize("failure", ["none", "bad_audience", "jwks_error"])
def test_beta_routes_need_token(client, settings, private_key, monkeypatch, host, method, path, extra, failure):
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP, APP + "."]
    if failure == "none":
        token = None
    elif failure == "bad_audience":
        token = make_token(private_key, aud=["other"])
    else:
        token = make_token(private_key)
        err = jwt.PyJWKClientConnectionError("down")
        monkeypatch.setattr(web.access, "_jwks_client", StubJWKS(None, error=err))
    headers = {"HTTP_CF_ACCESS_JWT_ASSERTION": token} if token else {}
    r = getattr(client, method)(path, HTTP_HOST=host, **extra, **headers)
    assert r.status_code == 403
    assert r.content == b"Access required"


@pytest.mark.django_db
@pytest.mark.parametrize("method,path,extra", APEX_ROUTES)
def test_apex_routes_need_no_token(client, categories, method, path, extra):
    r = getattr(client, method)(path, HTTP_HOST=PUBLIC, **extra)
    assert r.status_code == 200


@pytest.mark.django_db
def test_admin_on_beta_with_token(client, private_key):
    assert get(client, make_token(private_key), path="/admin/login/").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/admin/", "/search?q=green+tea"])
def test_unknown_host_still_400(client, path):
    assert client.get(path, HTTP_HOST="evil.example").status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/", "/admin/", "/admin/login/", "/lists/"])
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "web"])
def test_other_allowed_hosts_need_token(client, settings, private_key, host, path):
    # An allowed host that is neither public nor beta (e.g. the container's own name).
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP, "127.0.0.1", "localhost", "web"]
    assert client.get(path, HTTP_HOST=host).status_code == 403
    bad = make_token(private_key, aud=["other"])
    assert client.get(path, HTTP_HOST=host, HTTP_CF_ACCESS_JWT_ASSERTION=bad).status_code == 403
