import re
from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from waitlist.mail import record_send
from waitlist.models import Subscriber
from waitlist.tokens import confirm_token, unsubscribe_token

pytestmark = pytest.mark.django_db

PUBLIC = "unitpricer.com"
APP = "beta.unitpricer.com"


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.PUBLIC_HOST = PUBLIC
    settings.APP_HOST = APP
    settings.ALLOWED_HOSTS = [PUBLIC, f"www.{PUBLIC}", APP]
    settings.SECURE_SSL_REDIRECT = False


def signup(client, email="Person@Example.com", **extra):
    data = {"email": email, "website": ""}
    return client.post("/waitlist/", data, HTTP_HOST=PUBLIC, **extra)


def confirm_url_from(message):
    return re.search(r"https://unitpricer\.com(/waitlist/confirm/\S+)", message.body).group(1)


def test_home_has_notify_block(client):
    body = client.get("/", HTTP_HOST=PUBLIC).content.decode()
    assert "Notify me about new stores" in body and 'action="/waitlist/"' in body
    assert 'name="email"' in body and 'name="website"' in body
    assert "csrfmiddlewaretoken" not in body


def test_home_sets_no_cookies(client):
    r = client.get("/", HTTP_HOST=PUBLIC)
    assert not r.cookies


def test_home_signup_still_needs_confirm(client, mailoutbox):
    client.post("/waitlist/", {"email": "a@example.com"}, HTTP_HOST=PUBLIC)
    sub = Subscriber.objects.get(email="a@example.com")
    assert sub.confirmed_at is None and len(mailoutbox) == 1
    client.get(confirm_url_from(mailoutbox[0]), HTTP_HOST=PUBLIC)
    sub.refresh_from_db()
    assert sub.confirmed_at is None  # GET never confirms


@pytest.mark.parametrize(
    "extra",
    [
        {"HTTP_ORIGIN": "https://evil.example"},
        {"HTTP_ORIGIN": "null"},
        {"HTTP_ORIGIN": "http://unitpricer.com"},
        {"HTTP_REFERER": "https://evil.example/page"},
        {"HTTP_REFERER": "https://unitpricer.com.evil.example/"},
    ],
)
def test_signup_rejects_foreign_origin_or_referer(client, extra):
    r = client.post("/waitlist/", {"email": "a@example.com"}, HTTP_HOST=PUBLIC, **extra)
    assert r.status_code == 403 and not Subscriber.objects.exists()
    assert mail.outbox == []


@pytest.mark.parametrize(
    "extra",
    [
        {"HTTP_ORIGIN": "https://unitpricer.com"},
        {"HTTP_REFERER": "https://unitpricer.com/"},
        {},
    ],
)
def test_signup_same_origin_or_no_origin_ok(client, extra):
    r = client.post("/waitlist/", {"email": "b@example.com"}, HTTP_HOST=PUBLIC, **extra)
    assert r.status_code == 200


def test_signup_works_with_csrf_enforced_and_no_cookie():
    c = Client(enforce_csrf_checks=True)
    r = c.post("/waitlist/", {"email": "a@example.com"}, HTTP_HOST=PUBLIC)
    assert r.status_code == 200 and Subscriber.objects.count() == 1


def test_invalid_email_reshows_form(client):
    body = client.post("/waitlist/", {"email": "nope"}, HTTP_HOST=PUBLIC).content.decode()
    assert "Enter a valid email address." in body and 'action="/waitlist/"' in body


def test_landing_page_retired(client):
    body = client.get("/", HTTP_HOST=PUBLIC).content.decode()
    assert "Join the waitlist" not in body


def test_signup_creates_pending_and_sends_confirm(client):
    r = signup(client)
    assert r.status_code == 200
    sub = Subscriber.objects.get()
    assert sub.email == "person@example.com"
    assert sub.confirmed_at is None
    assert [m.to for m in mail.outbox] == [["person@example.com"]]


def test_signup_response_identical_for_new_pending_confirmed(client):
    new = signup(client, "a@example.com")
    Subscriber.objects.filter(email="a@example.com").update(
        confirm_sent_at=timezone.now() - timedelta(hours=1)
    )
    pending = signup(client, "a@example.com")
    Subscriber.objects.filter(email="a@example.com").update(confirmed_at=timezone.now())
    confirmed = signup(client, "a@example.com")
    assert new.status_code == pending.status_code == confirmed.status_code == 200
    assert new.content == pending.content == confirmed.content
    assert b"Check your inbox. If that address isn" in new.content


def test_honeypot_stores_and_sends_nothing(client):
    r = client.post(
        "/waitlist/",
        {"email": "bot@example.com", "website": "http://spam"},
        HTTP_HOST=PUBLIC,
    )
    assert r.status_code == 200
    assert Subscriber.objects.count() == 0
    assert mail.outbox == []


@pytest.mark.parametrize("email", ["nope", "", "a@b", "x" * 250 + "@example.com"])
def test_invalid_email_rerenders_form(client, email):
    r = signup(client, email)
    assert r.status_code == 200
    assert "Enter a valid email address." in r.content.decode()
    assert Subscriber.objects.count() == 0


def test_email_is_escaped_not_reflected(client):
    r = signup(client, '"><script>alert(1)</script>')
    assert b"<script>alert(1)</script>" not in r.content


def test_resend_throttled(client):
    signup(client)
    signup(client)  # within 10 minutes
    assert len(mail.outbox) == 1
    for _ in range(3):
        Subscriber.objects.update(confirm_sent_at=timezone.now() - timedelta(hours=1))
        signup(client)
    # 1st + 2nd resend allowed (3/day), later ones are not.
    assert len(mail.outbox) == 3


def test_signup_ip_limit_is_per_visitor(client, settings):
    settings.TRUST_CF_IP = True
    for i in range(5):
        r = signup(client, f"u{i}@example.com", HTTP_CF_CONNECTING_IP="203.0.113.1")
        assert r.status_code == 200
    r = signup(client, "u6@example.com", HTTP_CF_CONNECTING_IP="203.0.113.1")
    assert r.status_code == 429
    assert "Too many requests. Try again later." in r.content.decode()
    r = signup(client, "other@example.com", HTTP_CF_CONNECTING_IP="203.0.113.2")
    assert r.status_code == 200


def test_budget_exhausted_503_and_no_row(client, settings):
    settings.DAILY_EMAIL_CAP = 1
    record_send("confirm", timezone.now())
    r = signup(client)
    assert r.status_code == 503
    assert "We can't send confirmation emails right now." in r.content.decode()
    assert Subscriber.objects.count() == 0
    assert mail.outbox == []


def make(email="c@example.com", **kw):
    return Subscriber.objects.create(email=email, **kw)


def test_confirm_get_does_not_confirm(client):
    sub = make()
    r = client.get(f"/waitlist/confirm/{confirm_token(sub)}", HTTP_HOST=PUBLIC)
    assert r.status_code == 200
    assert "Confirm my signup" in r.content.decode()
    sub.refresh_from_db()
    assert sub.confirmed_at is None


def test_confirm_post_confirms_idempotent(client):
    sub = make()
    url = f"/waitlist/confirm/{confirm_token(sub)}"
    r = client.post(url, HTTP_HOST=PUBLIC)
    assert r.status_code == 200
    assert "You're on the list." in r.content.decode()
    sub.refresh_from_db()
    first = sub.confirmed_at
    assert first is not None
    client.post(url, HTTP_HOST=PUBLIC)
    sub.refresh_from_db()
    assert sub.confirmed_at == first


def test_expired_confirm_link_invalid(client, settings):
    sub = make()
    settings.WAITLIST_CONFIRM_MAX_AGE = timedelta(seconds=-1)
    for method in (client.get, client.post):
        r = method(f"/waitlist/confirm/{confirm_token(sub)}", HTTP_HOST=PUBLIC)
        assert r.status_code == 200
        assert "This link has expired or isn't valid." in r.content.decode()
    sub.refresh_from_db()
    assert sub.confirmed_at is None


def test_one_click_unsubscribe_without_csrf():
    c = Client(enforce_csrf_checks=True)
    sub = make()
    url = f"/waitlist/unsubscribe/{unsubscribe_token(sub)}"
    assert c.get(url, HTTP_HOST=PUBLIC).status_code == 200
    assert Subscriber.objects.count() == 1
    r = c.post(
        url,
        "List-Unsubscribe=One-Click",
        content_type="application/x-www-form-urlencoded",
        HTTP_HOST=PUBLIC,
    )
    assert r.status_code == 200
    assert "unsubscribed" in r.content.decode()
    assert Subscriber.objects.count() == 0


def test_unsubscribe_twice_ok(client):
    sub = make()
    url = f"/waitlist/unsubscribe/{unsubscribe_token(sub)}"
    assert client.post(url, HTTP_HOST=PUBLIC).status_code == 200
    r = client.post(url, HTTP_HOST=PUBLIC)
    assert r.status_code == 200
    assert "deleted" in r.content.decode()


def test_bad_unsubscribe_signature_404(client):
    assert (
        client.get("/waitlist/unsubscribe/garbage", HTTP_HOST=PUBLIC).status_code == 404
    )
    assert (
        client.post("/waitlist/unsubscribe/garbage", HTTP_HOST=PUBLIC).status_code
        == 404
    )


def test_signup_is_post_only(client):
    assert client.get("/waitlist/", HTTP_HOST=PUBLIC).status_code == 405


@pytest.mark.parametrize("host", [PUBLIC, APP])
def test_privacy_on_both_hosts(client, host):
    r = client.get("/privacy", HTTP_HOST=host)
    assert r.status_code == 200
    html = r.content.decode()
    assert "hello@unitpricer.com" in html and "Resend" in html
    assert "Last updated: 2026-11-08" in html
    for heading in (
        "What we collect",
        "Why",
        "Who handles it",
        "Cookies",
        "How long",
        "Your choices",
        "Changes",
        "Accounts and lists",
        "Feedback",
        "Logs",
    ):
        assert f">{heading}<" in html
    for name in ("DigitalOcean", "Cloudflare", "Resend", "R2", "GitHub", "Sentry"):
        assert name in html
    for needle in ("__Host-sessionid", "__Host-csrftoken", "ZIP code", "dante@unitpricer.com", "no IP addresses"):
        assert needle in html
    assert "Beta app" not in html and "the beta" not in html.lower().replace("staging", "")


def test_send_failure_shows_busy_page_and_removes_row(client, monkeypatch, caplog):
    import smtplib

    def boom(sub, now):
        raise smtplib.SMTPException("down")

    monkeypatch.setattr("waitlist.views.send_confirmation", boom)
    r = signup(client)
    assert r.status_code == 503
    assert "We can&#x27;t send confirmation emails right now." in r.content.decode() or (
        "We can't send confirmation emails right now." in r.content.decode()
    )
    assert Subscriber.objects.count() == 0
    assert "waitlist confirmation send failed: SMTPException" in caplog.text
    assert "person@example.com" not in caplog.text.lower()
    assert all(r.exc_info is None for r in caplog.records)


def test_budget_exhausted_is_busy_for_every_address(client, settings):
    settings.DAILY_EMAIL_CAP = 1
    record_send("confirm", timezone.now())
    Subscriber.objects.create(email="pending@example.com")
    done = Subscriber.objects.create(email="done@example.com", confirmed_at=timezone.now())
    assert done.pk
    for addr in ("new@example.com", "pending@example.com", "done@example.com"):
        assert signup(client, addr).status_code == 503
    assert not Subscriber.objects.filter(email="new@example.com").exists()
