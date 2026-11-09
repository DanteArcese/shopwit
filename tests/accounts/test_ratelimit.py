import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client

User = get_user_model()
PASSWORD = "correct-horse-battery-9"


@pytest.mark.django_db
def test_magic_per_email_limit_silent(client):
    pages = [
        Client().post("/account/magic-link", {"email": "a@example.com"}, REMOTE_ADDR=f"10.0.0.{i}")
        for i in range(4)
    ]
    assert len(mail.outbox) == 3
    assert {r.status_code for r in pages} == {200}
    assert len({re.sub(rb'value="[^"]+"', b'', r.content) for r in pages}) == 1
    # Another address is unaffected.
    Client().post("/account/magic-link", {"email": "b@example.com"})
    assert len(mail.outbox) == 4


@pytest.mark.django_db
def test_magic_per_ip_limit_429(client):
    for i in range(10):
        r = client.post("/account/magic-link", {"email": f"u{i}@example.com"})
        assert r.status_code == 200
    r = client.post("/account/magic-link", {"email": "u99@example.com"})
    assert r.status_code == 429
    assert b"Too many requests. Try again later." in r.content
    assert len(mail.outbox) == 10
    # A different client IP is unaffected.
    r = client.post("/account/magic-link", {"email": "u99@example.com"}, REMOTE_ADDR="10.9.9.9")
    assert r.status_code == 200


@pytest.mark.django_db
def test_magic_ip_uses_cf_connecting_ip(client, settings):
    settings.TRUST_CF_IP = True
    for i in range(10):
        client.post("/account/magic-link", {"email": f"u{i}@example.com"}, HTTP_CF_CONNECTING_IP="1.2.3.4")
    r = client.post("/account/magic-link", {"email": "x@example.com"}, HTTP_CF_CONNECTING_IP="1.2.3.4")
    assert r.status_code == 429
    r = client.post("/account/magic-link", {"email": "x@example.com"}, HTTP_CF_CONNECTING_IP="5.6.7.8")
    assert r.status_code == 200


@pytest.mark.django_db
def test_password_failures_limited():
    User.objects.create_user(username="a@example.com", email="a@example.com", password=PASSWORD)
    bad = {"email": "a@example.com", "password": "wrong-pass-1"}
    good = {"email": "a@example.com", "password": PASSWORD}
    for i in range(5):
        r = Client().post("/account/sign-in", bad, REMOTE_ADDR=f"10.0.1.{i}")
        assert b"Email or password is incorrect." in r.content
    c = Client()
    r = c.post("/account/sign-in", good, REMOTE_ADDR="10.0.2.1")
    assert r.status_code == 200
    assert b"Email or password is incorrect." in r.content
    assert "_auth_user_id" not in c.session
    cache.clear()  # the window passes
    r = c.post("/account/sign-in", good, REMOTE_ADDR="10.0.2.1")
    assert r.status_code == 302


@pytest.mark.django_db
def test_password_failures_limited_per_ip():
    User.objects.create_user(username="a@example.com", email="a@example.com", password=PASSWORD)
    for i in range(20):
        Client().post("/account/sign-in", {"email": f"x{i}@example.com", "password": "bad-pass-1"})
    c = Client()
    r = c.post("/account/sign-in", {"email": "a@example.com", "password": PASSWORD})
    assert b"Email or password is incorrect." in r.content
    assert "_auth_user_id" not in c.session
    r = c.post("/account/sign-in", {"email": "a@example.com", "password": PASSWORD}, REMOTE_ADDR="10.3.3.3")
    assert r.status_code == 302


@pytest.mark.django_db
def test_successful_sign_ins_are_not_counted():
    User.objects.create_user(username="a@example.com", email="a@example.com", password=PASSWORD)
    for _ in range(8):
        assert Client().post("/account/sign-in", {"email": "a@example.com", "password": PASSWORD}).status_code == 302
