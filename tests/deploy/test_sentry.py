import json

import pytest

from config import sentry
from config.sentry import init_sentry, scrub_event

TOKEN = "Mq3:1abc:Zx9-secret"


def _event():
    return {
        "message": "Failed for dante@example.com at /account/magic/" + TOKEN,
        "logentry": {
            "message": "bad %s",
            "formatted": "bad person@example.org",
            "params": ["person@example.org"],
        },
        "server_name": "droplet-1",
        "user": {"id": 5, "email": "a@b.co", "ip_address": "1.2.3.4", "username": "dante"},
        "request": {
            "url": f"https://unitpricer.com/waitlist/confirm/{TOKEN}?next=/x&q=my+address#frag",
            "method": "POST",
            "query_string": "q=my+address",
            "cookies": {"__Host-sessionid": "sess"},
            "headers": {
                "Cookie": "__Host-sessionid=sess",
                "Authorization": "Bearer t",
                "Cf-Access-Jwt-Assertion": "jwt",
                "Cf-Connecting-Ip": "1.2.3.4",
                "X-Forwarded-For": "1.2.3.4",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://unitpricer.com/search?q=secret",
            },
            "data": {"email": "a@b.co", "website": ""},
            "env": {"REMOTE_ADDR": "1.2.3.4", "SERVER_NAME": "unitpricer.com"},
        },
        "breadcrumbs": {
            "values": [
                {"category": "query", "message": "SELECT 1 -- owner x@y.zz"},
                {
                    "category": "httplib",
                    "message": "GET https://api.example/p?email=x@y.zz",
                    "data": {"url": "https://api.example/p?email=x@y.zz", "http.query": "email=x@y.zz"},
                },
            ]
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "no user for leo@example.com",
                    "stacktrace": {
                        "frames": [
                            {"function": "f", "lineno": 3, "vars": {"email": "a@b.co"}},
                        ]
                    },
                }
            ]
        },
        "extra": {"note": "cc boss@example.com"},
        "tags": {"url": "https://unitpricer.com/search?q=x"},
    }


def test_no_dsn_no_init(monkeypatch):
    called = []
    monkeypatch.setattr(sentry.sentry_sdk, "init", lambda *a, **k: called.append(k))
    assert init_sentry("") is False
    assert init_sentry(None) is False
    assert called == []


def test_init_passes_privacy_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(sentry.sentry_sdk, "init", lambda *a, **k: captured.update(k))
    assert init_sentry("https://k@o0.ingest.sentry.io/1", environment="beta") is True
    assert captured["send_default_pii"] is False
    assert captured["traces_sample_rate"] == 0
    assert captured["before_send"] is scrub_event
    assert captured["environment"] == "beta"
    assert captured["include_local_variables"] is False
    assert captured["max_request_body_size"] == "never"
    assert captured["server_name"] == ""


def test_settings_do_not_init_without_dsn():
    from django.conf import settings

    assert settings.SENTRY_DSN is None


def test_scrub_removes_every_pii_field():
    out = scrub_event(_event(), {})
    req = out["request"]
    for key in ("query_string", "cookies", "data", "env"):
        assert key not in req
    assert "user" not in out
    assert out["server_name"] == ""
    assert req["headers"] == {"User-Agent": "Mozilla/5.0"}
    assert req["url"] == "https://unitpricer.com/waitlist/confirm/…"
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    crumbs = out["breadcrumbs"]["values"]
    assert "http.query" not in crumbs[1]["data"]
    blob = json.dumps(out)
    for secret in ("@", TOKEN, "1.2.3.4", "sess", "my+address", "q=x", "Bearer", "jwt", "secret"):
        assert secret not in blob, secret
    assert "ValueError" in blob and "Mozilla/5.0" in blob
    assert "[email]" in out["message"]


def test_scrub_tolerates_sparse_and_odd_events():
    assert scrub_event({}, {}) == {}
    out = scrub_event({"request": {"headers": [("Cookie", "x"), ("User-Agent", "ua")]}}, {})
    assert out["request"]["headers"] == {"User-Agent": "ua"}


def test_scrub_never_raises(monkeypatch):
    monkeypatch.setattr(sentry, "_scrub_value", lambda v: 1 / 0)
    assert scrub_event({"message": "x"}, {}) is None
