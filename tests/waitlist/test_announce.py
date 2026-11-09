import smtplib
from collections import Counter
from datetime import timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from waitlist.mail import send_confirmation
from waitlist.models import EmailSend, Subscriber

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def conf(settings):
    settings.DAILY_EMAIL_CAP = 10
    settings.WAITLIST_CONFIRM_RESERVE = 3
    settings.POSTAL_ADDRESS = "PO Box 1, Boston MA"
    settings.SITE_URL = "https://unitpricer.com"


def make(n, confirmed=True, prefix="u"):
    now = timezone.now()
    for i in range(n):
        Subscriber.objects.create(
            email=f"{prefix}{i}@example.com",
            confirmed_at=now + timedelta(seconds=i) if confirmed else None,
        )


def run(*args):
    out = StringIO()
    call_command("send_waitlist_announcement", "--subject", "Open", "--pause", "0", *args, stdout=out)
    return out.getvalue()


def test_announce_leaves_confirm_reserve():
    make(12)
    out = run()
    assert "sent 7, remaining 5" in out
    assert "budget reached; run again tomorrow" in out
    assert len(mail.outbox) == 7
    pending = Subscriber.objects.create(email="new@example.com")
    assert send_confirmation(pending, timezone.now()) is True
    for _ in range(2):
        assert send_confirmation(pending, timezone.now()) is True
    assert send_confirmation(pending, timezone.now()) is False  # 11th of any kind


def test_announce_rerun_sends_no_duplicates():
    make(12)
    run()
    EmailSend.objects.update(sent_at=timezone.now() - timedelta(hours=25))
    out = run()
    assert "sent 5, remaining 0" in out
    counts = Counter(m.to[0] for m in mail.outbox)
    assert len(counts) == 12
    assert set(counts.values()) == {1}


def test_announce_skips_pending_and_unsubscribed():
    make(2)
    make(2, confirmed=False, prefix="p")
    Subscriber.objects.create(email="done@example.com", confirmed_at=timezone.now(), announced_at=timezone.now())
    run()
    assert sorted(m.to[0] for m in mail.outbox) == ["u0@example.com", "u1@example.com"]


def test_announce_requires_postal_address(settings):
    settings.POSTAL_ADDRESS = ""
    make(1)
    with pytest.raises(CommandError):
        run()
    assert mail.outbox == []


def test_announce_requires_public_url(settings):
    settings.PUBLIC_URL = ""
    settings.DEBUG = False
    with pytest.raises(CommandError):
        run()


def test_announce_requires_site_url(settings):
    settings.SITE_URL = ""
    settings.DEBUG = False
    make(1)
    with pytest.raises(CommandError, match="SITE_URL"):
        run()
    assert mail.outbox == []


def test_limit():
    make(5)
    out = run("--limit", "2")
    assert "sent 2, remaining 3" in out


def test_smtp_failure_stops_and_marks_sent_ones(monkeypatch):
    make(5)
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_messages(self, messages):
            calls.append(messages)
            if len(calls) == 3:
                raise smtplib.SMTPException("boom")
            return len(messages)

    monkeypatch.setattr(
        "waitlist.management.commands.send_waitlist_announcement.get_connection", lambda: Conn()
    )
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("send_waitlist_announcement", "--subject", "Open", "--pause", "0", stdout=out)
    assert "sent 2" in out.getvalue()
    assert Subscriber.objects.filter(announced_at__isnull=False).count() == 2
    assert EmailSend.objects.count() == 2


def test_dry_run_sends_nothing():
    make(3)
    out = run("--dry-run")
    assert "sent 0, remaining 3" in out
    assert "Unsubscribe:" in out
    assert mail.outbox == []
    assert EmailSend.objects.count() == 0
    assert Subscriber.objects.filter(announced_at__isnull=False).count() == 0


def test_announce_email_footer():
    make(1)
    run()
    m = mail.outbox[0]
    assert "You're receiving this because you joined the UnitPricer waitlist at unitpricer.com.\n" in m.body
    assert "\nUnsubscribe: https://unitpricer.com/waitlist/unsubscribe/" in m.body
    assert m.body.rstrip().endswith("PO Box 1, Boston MA")
    assert "https://unitpricer.com" in m.body and "beta." not in m.body
    assert m.extra_headers["List-Unsubscribe"].startswith("<https://unitpricer.com/waitlist/unsubscribe/")
    assert m.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
