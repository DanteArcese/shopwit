import hashlib
import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from accounts.magic import consume_token, issue_token, peek_token
from accounts.models import MagicLinkToken

NOW = timezone.now().replace(microsecond=0)


@pytest.mark.django_db
def test_token_stored_hashed():
    raw = issue_token("a@example.com", "/lists/", NOW)
    assert len(raw) >= 43
    row = MagicLinkToken.objects.get()
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    for field in MagicLinkToken._meta.concrete_fields:
        assert raw not in str(getattr(row, field.attname))
    with connection.cursor() as cur:
        cur.execute("SELECT * FROM accounts_magiclinktoken")
        assert all(raw not in str(col) for col in cur.fetchone())


@pytest.mark.django_db
def test_token_single_use():
    raw = issue_token("a@example.com", "/lists/", NOW)
    first = consume_token(raw, NOW + timedelta(minutes=1))
    assert first is not None and first.email == "a@example.com"
    assert consume_token(raw, NOW + timedelta(minutes=2)) is None
    assert peek_token(raw, NOW + timedelta(minutes=2)) is None


@pytest.mark.django_db
def test_token_expires():
    raw = issue_token("a@example.com", "/lists/", NOW)
    assert consume_token(raw, NOW + timedelta(minutes=15, seconds=1)) is None
    assert peek_token(raw, NOW + timedelta(minutes=15, seconds=1)) is None
    raw2 = issue_token("a@example.com", "/lists/", NOW)
    assert consume_token(raw2, NOW + timedelta(minutes=14)) is not None


@pytest.mark.django_db
def test_peek_does_not_consume():
    raw = issue_token("a@example.com", "/lists/", NOW)
    assert peek_token(raw, NOW) is not None
    assert peek_token(raw, NOW) is not None
    assert consume_token(raw, NOW) is not None


@pytest.mark.django_db
def test_unknown_or_garbage_token():
    assert peek_token("nope", NOW) is None
    assert consume_token("nope", NOW) is None
    assert consume_token("x" * 5000, NOW) is None
    assert consume_token("", NOW) is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_consume_only_one_wins():
    raw = issue_token("a@example.com", "/lists/", NOW)
    barrier = threading.Barrier(4)
    results = []

    def worker():
        try:
            barrier.wait()
            results.append(consume_token(raw, NOW))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1


@pytest.mark.django_db
def test_old_tokens_purged_on_issue():
    old = issue_token("old@example.com", "/lists/", NOW - timedelta(days=2))
    recent = issue_token("recent@example.com", "/lists/", NOW - timedelta(hours=12))
    issue_token("new@example.com", "/lists/", NOW)
    emails = set(MagicLinkToken.objects.values_list("email", flat=True))
    assert emails == {"recent@example.com", "new@example.com"}
    assert peek_token(old, NOW) is None and recent
