import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import web.access

PUBLIC = "unitpricer.com"
APP = "beta.unitpricer.com"
TEAM = "unitpricer.cloudflareaccess.com"
AUD = "test-aud"


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
def test_inactive_without_aud(client, settings):
    settings.CF_ACCESS_AUD = ""
    assert get(client).status_code == 200


def test_trailing_dot_host_still_checked(client):
    assert get(client, host=APP + ".").status_code == 403


def test_token_without_exp_403(client, private_key):
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "email": "me@example.com"}
    token = jwt.encode(claims, private_key, algorithm="RS256")
    assert get(client, token).status_code == 403


@pytest.mark.django_db
def test_trailing_dot_public_hosts(client, settings):
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP]
    assert get(client, path="/c/dairy/milk/", host=PUBLIC + ".").status_code == 404
    assert get(client, host=PUBLIC + ".").status_code == 200
    r = get(client, host=f"www.{PUBLIC}.")
    assert r.status_code == 301
