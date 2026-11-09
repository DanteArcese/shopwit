import logging
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.utils import timezone

from accounts.models import MagicLinkToken
from lists.models import ShoppingList

User = get_user_model()
PASSWORD = "correct-horse-battery-9"
LINK = re.compile(r"https://\S+/account/magic/([A-Za-z0-9_-]+)")


def _request_link(client, email="new@example.com", **extra):
    return client.post("/account/magic-link", {"email": email, **extra})


def _token_from_outbox(i=-1):
    return LINK.search(mail.outbox[i].body).group(1)


_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


def _body(response):
    """Page content without the per-render masked CSRF token."""
    return _CSRF.sub(b"", response.content)


def _user(email="old@example.com", password=PASSWORD):
    return User.objects.create_user(username=email, email=email, password=password)


@pytest.mark.django_db
def test_sign_in_page_has_both_forms(client):
    r = client.get("/account/sign-in?next=/lists/")
    assert r.status_code == 200
    html = r.content.decode()
    assert "Email me a sign-in link" in html
    assert "Sign in with a password" in html
    assert "No account? Enter your email for a sign-in link; that creates one." in html


@pytest.mark.django_db
def test_magic_link_email_sent_with_site_url(client, settings):
    settings.SITE_URL = "https://unitpricer.test"
    r = _request_link(client, HTTP_HOST="evil.example")
    assert r.status_code == 200
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["new@example.com"]
    assert msg.subject == "Your UnitPricer sign-in link"
    assert "https://unitpricer.test/account/magic/" in msg.body
    assert "evil.example" not in msg.body
    assert "It works once and expires in 15 minutes." in msg.body
    assert "If you didn't ask for it, ignore this email." in msg.body


@pytest.mark.django_db
def test_magic_link_counts_against_email_budget(client):
    from waitlist.models import EmailSend

    _request_link(client)
    assert list(EmailSend.objects.values_list("kind", flat=True)) == ["magic"]


@pytest.mark.django_db
def test_budget_exhausted_shows_try_later_page(client, settings):
    settings.DAILY_EMAIL_CAP = 0
    r_new = _request_link(client, "new@example.com")
    _user("old@example.com")
    r_old = _request_link(client, "old@example.com")
    assert len(mail.outbox) == 0
    assert r_new.status_code == r_old.status_code == 503
    assert _body(r_new) == _body(r_old)
    assert b"can't send email right now" in r_new.content
    # The unsent token isn't left usable.
    assert MagicLinkToken.objects.count() == 0


@pytest.mark.django_db
def test_smtp_failure_shows_try_later_page_without_logging_email(client, monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("connection refused for new@example.com")

    monkeypatch.setattr("accounts.magic.send_budgeted", boom)
    with caplog.at_level(logging.DEBUG):
        r = _request_link(client, "new@example.com")
    assert r.status_code == 503
    assert "new@example.com" not in caplog.text
    assert MagicLinkToken.objects.count() == 0


@pytest.mark.django_db
def test_get_does_not_consume(client):
    _request_link(client)
    token = _token_from_outbox()
    for _ in range(2):  # a mail scanner, then the person
        r = Client().get(f"/account/magic/{token}")
        assert r.status_code == 200
        assert b"Sign in" in r.content and b'method="post"' in r.content
    assert MagicLinkToken.objects.get().used_at is None
    r = client.post(f"/account/magic/{token}")
    assert r.status_code == 302
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_post_consumes_and_creates_user(client):
    client.get("/account/sign-in")  # starts a session (CSRF cookie only, no session yet)
    session = client.session
    session["probe"] = 1
    session.save()
    before = session.session_key
    _request_link(client, "New@Example.com", next="/lists/")
    token = _token_from_outbox()
    r = client.post(f"/account/magic/{token}")
    assert r.status_code == 302 and r["Location"] == "/lists/"
    user = User.objects.get()
    assert user.username == user.email == "new@example.com"
    assert not user.has_usable_password()
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session.session_key != before


@pytest.mark.django_db
def test_magic_link_signs_in_existing_user(client):
    user = _user("old@example.com")
    _request_link(client, "old@example.com")
    r = client.post(f"/account/magic/{_token_from_outbox()}")
    assert r.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    assert User.objects.count() == 1
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_used_or_expired_link_shows_invalid_page(client, monkeypatch):
    _request_link(client)
    token = _token_from_outbox()
    assert client.post(f"/account/magic/{token}").status_code == 302
    used_post = Client().post(f"/account/magic/{token}")
    used_get = Client().get(f"/account/magic/{token}")

    _request_link(client, "other@example.com")
    token2 = _token_from_outbox()
    later = timezone.now() + timedelta(minutes=15, seconds=1)
    monkeypatch.setattr("accounts.views._now", lambda: later)
    expired_post = Client().post(f"/account/magic/{token2}")
    expired_get = Client().get(f"/account/magic/{token2}")
    garbage = Client().post("/account/magic/garbage")

    pages = [used_post, used_get, expired_post, expired_get, garbage]
    for r in pages:
        assert r.status_code == 200
        assert b"This link has expired or was already used." in r.content
        assert b"/account/sign-in" in r.content
    assert len({r.content for r in pages}) == 1
    assert token.encode() not in used_get.content


@pytest.mark.django_db
def test_confirm_page_does_not_echo_token_outside_form(client):
    _request_link(client)
    token = _token_from_outbox()
    html = client.get(f"/account/magic/{token}").content.decode()
    assert html.count(token) == 1
    assert f'action="/account/magic/{token}"' in html
    assert User.objects.filter(email="other@example.com").count() == 0


@pytest.mark.django_db
def test_magic_link_refused_for_staff(client):
    staff = _user("admin@example.com")
    staff.is_staff = True
    staff.save()
    _request_link(client, "admin@example.com")
    r = client.post(f"/account/magic/{_token_from_outbox()}")
    assert b"This link has expired or was already used." in r.content
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_no_user_enumeration(client):
    _user("old@example.com")
    r_old = Client().post("/account/magic-link", {"email": "old@example.com"})
    r_new = Client().post("/account/magic-link", {"email": "nobody@example.com"})
    assert r_old.status_code == r_new.status_code == 200
    assert _body(r_old) == _body(r_new)
    assert b"If that address can receive email, a sign-in link is on its way." in r_old.content

    wrong = Client().post("/account/sign-in", {"email": "old@example.com", "password": "nope-nope-1"})
    unknown = Client().post("/account/sign-in", {"email": "nobody@example.com", "password": "nope-nope-1"})
    assert wrong.status_code == unknown.status_code == 200
    assert _body(wrong) == _body(unknown)
    assert b"Email or password is incorrect." in wrong.content


@pytest.mark.django_db
def test_magic_only_account_password_sign_in_same_error(client):
    User.objects.create_user(username="m@example.com", email="m@example.com")  # unusable password
    r = Client().post("/account/sign-in", {"email": "m@example.com", "password": "anything-1"})
    r2 = Client().post("/account/sign-in", {"email": "zz@example.com", "password": "anything-1"})
    assert _body(r) == _body(r2)


@pytest.mark.django_db
def test_invalid_email_magic_request(client):
    r = client.post("/account/magic-link", {"email": "not an email"})
    assert r.status_code == 200
    assert b"Enter a valid email address." in r.content
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_password_sign_in_and_safe_next(client):
    user = _user()
    client.get("/account/sign-in")
    s = client.session
    s["probe"] = 1
    s.save()
    before = s.session_key
    r = client.post(
        "/account/sign-in?next=https://evil.example/",
        {"email": " OLD@example.com ", "password": PASSWORD},
    )
    assert r.status_code == 302 and r["Location"] == "/"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session.session_key != before

    c = Client()
    r = c.post("/account/sign-in", {"email": "old@example.com", "password": PASSWORD, "next": "/lists/"})
    assert r["Location"] == "/lists/"
    for bad in ("//evil.example/", "/\\evil.example", "javascript:alert(1)", "https://evil.example/x"):
        c = Client()
        r = c.post("/account/sign-in", {"email": "old@example.com", "password": PASSWORD, "next": bad})
        assert r["Location"] == "/", bad


@pytest.mark.django_db
def test_magic_link_next_is_sanitized(client):
    _request_link(client, next="https://evil.example/")
    r = client.post(f"/account/magic/{_token_from_outbox()}")
    assert r["Location"] == "/lists/"


@pytest.mark.django_db
def test_magic_link_only_account_can_set_password(client):
    _request_link(client, "new@example.com")
    client.post(f"/account/magic/{_token_from_outbox()}")
    r = client.get("/account/")
    assert r.status_code == 200
    assert b"new@example.com" in r.content
    assert b"Set a password" in r.content
    assert b"old_password" not in r.content
    # Validators are on.
    r = client.post("/account/password", {"new_password1": "123", "new_password2": "123"})
    assert r.status_code == 200
    assert not User.objects.get().has_usable_password()
    r = client.post("/account/password", {"new_password1": PASSWORD, "new_password2": PASSWORD})
    assert r.status_code == 302
    user = User.objects.get()
    assert user.check_password(PASSWORD)
    # Still signed in after the password change (session auth hash updated).
    assert client.get("/account/").status_code == 200
    # Now it's a change form that needs the old password.
    assert b"old_password" in client.get("/account/").content
    r = client.post(
        "/account/password",
        {"old_password": "wrong", "new_password1": "x-Another-pass-77", "new_password2": "x-Another-pass-77"},
    )
    assert r.status_code == 200
    assert User.objects.get().check_password(PASSWORD)


@pytest.mark.django_db
def test_account_requires_login(client):
    r = client.get("/account/")
    assert r.status_code == 302
    assert r["Location"].startswith("/account/sign-in?next=")


@pytest.mark.django_db
def test_sign_out_requires_post(client):
    user = _user()
    client.force_login(user)
    assert client.get("/account/sign-out").status_code == 405
    assert "_auth_user_id" in client.session
    r = client.post("/account/sign-out")
    assert r.status_code == 302 and r["Location"] == "/"
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_delete_account_cascades_lists(client):
    user = _user()
    other = _user("keep@example.com")
    ShoppingList.objects.create(user=user, uid="a" * 32, name="Mine")
    ShoppingList.objects.create(user=other, uid="b" * 32, name="Theirs")
    client.force_login(user)
    r = client.post("/account/delete", {"confirm": "nope"})
    assert r.status_code == 200
    assert User.objects.filter(pk=user.pk).exists()
    assert client.get("/account/delete").status_code == 405
    r = client.post("/account/delete", {"confirm": "delete"})
    assert r.status_code == 302 and r["Location"] == "/"
    assert not User.objects.filter(pk=user.pk).exists()
    assert list(ShoppingList.objects.values_list("name", flat=True)) == ["Theirs"]
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_auth_posts_require_csrf():
    user = _user()
    c = Client(enforce_csrf_checks=True)
    for path, data in [
        ("/account/sign-in", {"email": "old@example.com", "password": PASSWORD}),
        ("/account/magic-link", {"email": "old@example.com"}),
        (f"/account/magic/{'a' * 43}", {}),
    ]:
        assert c.post(path, data).status_code == 403, path
    c.force_login(user)
    for path in ("/account/sign-out", "/account/password", "/account/delete"):
        assert c.post(path, {"confirm": "delete"}).status_code == 403, path
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db
def test_header_shows_sign_in_or_account(client):
    html = client.get("/account/sign-in").content.decode()
    assert 'href="/account/sign-in' in html
    client.force_login(_user())
    html = client.get("/account/").content.decode()
    assert 'href="/account/"' in html


@pytest.mark.django_db
def test_account_routes_served_on_apex(client, settings):
    # Launch (#77): the apex is the public app, so accounts work there too (admin does not).
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    for method, path in [
        ("get", "/account/"),
        ("get", "/account/sign-in"),
        ("post", "/account/sign-in"),
        ("post", "/account/magic-link"),
        ("get", f"/account/magic/{'a' * 43}"),
        ("post", f"/account/magic/{'a' * 43}"),
        ("get", "/account/magic/x"),
        ("post", "/account/magic/x"),
        ("post", "/account/sign-out"),
    ]:
        apex = getattr(client, method)(path, HTTP_HOST="unitpricer.com")
        beta = getattr(client, method)(path, HTTP_HOST="beta.unitpricer.com")
        assert apex.status_code != 404 and apex.status_code == beta.status_code, path
    assert client.get("/account/sign-in", HTTP_HOST="unitpricer.com").status_code == 200
    assert client.get("/admin/login/", HTTP_HOST="unitpricer.com").status_code == 404


@pytest.mark.django_db
def test_auth_pages_not_cached(client):
    assert "private" in client.get("/account/sign-in")["Cache-Control"]
    _request_link(client)
    r = client.get(f"/account/magic/{_token_from_outbox()}")
    assert "private" in r["Cache-Control"] or "no-store" in r["Cache-Control"]


@pytest.mark.django_db
def test_no_emails_or_tokens_logged(client, caplog):
    with caplog.at_level(logging.DEBUG):
        _request_link(client, "secret@example.com")
        token = _token_from_outbox()
        client.get(f"/account/magic/{token}")
        client.post(f"/account/magic/{token}")
        client.post(f"/account/magic/{token}")
        Client().post("/account/sign-in", {"email": "secret@example.com", "password": "bad-pass-1"})
    assert "secret@example.com" not in caplog.text
    assert token not in caplog.text


@pytest.mark.parametrize("host", ["unitpricer.com", "beta.unitpricer.com"])
def test_privacy_page_accounts_section(client, settings, host):
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    html = client.get("/privacy", HTTP_HOST=host).content.decode()
    assert ">Accounts and lists<" in html
    section = html.split(">Accounts and lists<", 1)[1].split("<h2", 1)[0]
    for phrase in ("email address", "password", "lists", "settings", "Delete account", "15 minutes"):
        assert phrase in section, phrase


@pytest.mark.django_db
def test_delete_account_deletes_its_magic_link_tokens(client):
    user = _user("gone@example.com")
    _request_link(client, "gone@example.com")
    _request_link(client, "keep@example.com")
    client.force_login(user)
    client.post("/account/delete", {"confirm": "delete"})
    assert list(MagicLinkToken.objects.values_list("email", flat=True)) == ["keep@example.com"]


@pytest.mark.django_db
def test_magic_link_fails_closed_without_site_url(client, settings):
    settings.DEBUG = False
    settings.SITE_URL = ""
    r = _request_link(client, "a@example.com")
    assert r.status_code == 503
    assert b"can't send email right now" in r.content
    assert len(mail.outbox) == 0
    assert MagicLinkToken.objects.count() == 0


@pytest.mark.django_db
def test_magic_link_debug_without_site_url_uses_localhost(client, settings):
    settings.DEBUG = True
    settings.SITE_URL = ""
    _request_link(client, "a@example.com")
    assert "http://localhost:8000/account/magic/" in mail.outbox[0].body


@pytest.mark.django_db
def test_confirm_page_names_the_account(client):
    _request_link(client, "Who@Example.com")
    html = client.get(f"/account/magic/{_token_from_outbox()}").content.decode()
    assert "Sign in as" in html and "who@example.com" in html


@pytest.mark.django_db
def test_magic_links_leave_the_confirmation_reserve(client, settings):
    from waitlist.mail import send_budgeted
    from waitlist.models import EmailSend
    from django.core.mail import EmailMessage

    settings.DAILY_EMAIL_CAP = 5
    settings.WAITLIST_CONFIRM_RESERVE = 2
    now = timezone.now()
    for _ in range(3):
        EmailSend.objects.create(sent_at=now, kind="announce")
    r = _request_link(client, "new@example.com")
    assert r.status_code == 503 and len(mail.outbox) == 0
    # The reserve is still there for waitlist confirmations.
    assert send_budgeted(EmailMessage(to=["w@example.com"]), "confirm", now) is True


@pytest.mark.django_db
def test_sign_in_password_is_sensitive(client):
    r = client.post("/account/sign-in", {"email": "x@example.com", "password": "hunter2-hunter2"})
    assert r.wsgi_request.sensitive_post_parameters == ("password",)


@pytest.mark.django_db
def test_password_change_fields_are_sensitive(client):
    client.force_login(_user())
    r = client.post("/account/password", {"old_password": "x", "new_password1": "y", "new_password2": "y"})
    assert set(r.wsgi_request.sensitive_post_parameters) == {
        "old_password",
        "new_password1",
        "new_password2",
    }


def test_privacy_page_names_the_cookies(client):
    html = client.get("/privacy").content.decode()
    assert "Beta app: a session cookie and a security (CSRF) cookie." in html
    assert "settings cookie" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["superuser", "staff", "inactive"])
def test_privileged_or_inactive_password_sign_in_fails_on_apex(client, settings, kind):
    """#77: the public apex never mints a staff session; admin login stays on beta."""
    from django.core.cache import cache

    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    email = "root@example.com"
    if kind == "superuser":
        User.objects.create_superuser(email, email, PASSWORD)
    else:
        user = _user(email)
        user.is_staff = kind == "staff"
        user.is_active = kind != "inactive"
        user.save()
    wrong = client.post("/account/sign-in", {"email": email, "password": "wrong-pass-1"}, HTTP_HOST="unitpricer.com")
    cache.clear()
    r = client.post("/account/sign-in", {"email": email, "password": PASSWORD}, HTTP_HOST="unitpricer.com")
    assert r.status_code == 200
    assert "Email or password is incorrect." in r.content.decode()
    assert _body(r) == _body(wrong)  # indistinguishable from a wrong password
    assert settings.SESSION_COOKIE_NAME not in r.cookies
    assert "_auth_user_id" not in client.session
    # Counted as a failure: the per-email lockout applies.
    for _ in range(5):
        client.post("/account/sign-in", {"email": email, "password": PASSWORD}, HTTP_HOST="unitpricer.com")
    from web.ratelimit import exceeded
    from accounts.views import SIGNIN_EMAIL_LIMIT

    assert exceeded("signin-fail-email", email, *SIGNIN_EMAIL_LIMIT)


@pytest.mark.django_db
def test_superuser_admin_login_still_works_on_beta(client, settings):
    settings.PUBLIC_HOST = "unitpricer.com"
    settings.APP_HOST = "beta.unitpricer.com"
    User.objects.create_superuser("root@example.com", "root@example.com", PASSWORD)
    r = client.post(
        "/admin/login/?next=/admin/",
        {"username": "root@example.com", "password": PASSWORD, "next": "/admin/"},
        HTTP_HOST="beta.unitpricer.com",
    )
    assert r.status_code == 302 and r["Location"] == "/admin/"
    assert client.get("/admin/", HTTP_HOST="beta.unitpricer.com").status_code == 200
    assert client.get("/admin/", HTTP_HOST="unitpricer.com").status_code == 404
