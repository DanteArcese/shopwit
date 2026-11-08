import time
from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, transaction

from waitlist.models import Subscriber
from waitlist.tokens import (
    confirm_token,
    read_confirm_token,
    read_unsubscribe_token,
    unsubscribe_token,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def make(email="a@example.com", age=timedelta(0), confirmed=False):
    sub = Subscriber.objects.create(email=email)
    Subscriber.objects.filter(pk=sub.pk).update(
        created_at=NOW - age, confirmed_at=NOW if confirmed else None
    )
    sub.refresh_from_db()
    return sub


def test_email_unique():
    make()
    with pytest.raises(IntegrityError), transaction.atomic():
        Subscriber.objects.create(email="a@example.com")


def test_confirm_token_round_trip():
    sub = make()
    assert read_confirm_token(confirm_token(sub)) == sub


def test_confirm_token_expires(monkeypatch):
    sub = make()
    token = confirm_token(sub)
    later = time.time() + 7 * 86400 + 1
    monkeypatch.setattr("django.core.signing.time.time", lambda: later)
    assert read_confirm_token(token) is None


def test_confirm_token_valid_before_expiry(monkeypatch):
    sub = make()
    token = confirm_token(sub)
    later = time.time() + 6 * 86400
    monkeypatch.setattr("django.core.signing.time.time", lambda: later)
    assert read_confirm_token(token) == sub


def test_confirm_token_rejects_other_salt():
    sub = make()
    assert read_confirm_token(unsubscribe_token(sub)) is None
    assert read_unsubscribe_token(confirm_token(sub)) == (False, None)


def test_tampered_token_none():
    sub = make()
    token = confirm_token(sub)
    bad = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert read_confirm_token(bad) is None
    assert read_confirm_token("garbage") is None
    assert read_unsubscribe_token("garbage") == (False, None)


def test_confirm_token_missing_row():
    sub = make()
    token = confirm_token(sub)
    sub.delete()
    assert read_confirm_token(token) is None


def test_confirm_token_email_mismatch():
    sub = make()
    token = confirm_token(sub)
    Subscriber.objects.filter(pk=sub.pk).update(email="other@example.com")
    assert read_confirm_token(token) is None


def test_token_payload_does_not_contain_email():
    from django.core import signing

    sub = make("secret.person@example.com")
    for token, salt in ((confirm_token(sub), "waitlist.confirm"), (unsubscribe_token(sub), "waitlist.unsubscribe")):
        data = signing.loads(token, salt=salt)
        blob = repr(data) + token
        assert "secret.person" not in blob and "example.com" not in blob
        assert "e" not in data


def test_unsubscribe_token_round_trip():
    sub = make()
    assert read_unsubscribe_token(unsubscribe_token(sub)) == (True, sub)


def test_unsubscribe_token_for_deleted_row():
    sub = make()
    token = unsubscribe_token(sub)
    sub.delete()
    assert read_unsubscribe_token(token) == (True, None)


def test_purge_unconfirmed():
    old_pending = make("old@example.com", age=timedelta(days=8))
    old_confirmed = make("oc@example.com", age=timedelta(days=8), confirmed=True)
    new_pending = make("new@example.com", age=timedelta(days=6))
    assert Subscriber.purge_unconfirmed(NOW) == 1
    remaining = set(Subscriber.objects.values_list("pk", flat=True))
    assert remaining == {old_confirmed.pk, new_pending.pk}
    assert old_pending.pk not in remaining
