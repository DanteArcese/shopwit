from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail
from django.core.mail import EmailMessage

from waitlist.mail import record_send, send_budgeted, send_confirmation, sends_in_last_day
from waitlist.models import EmailSend, Subscriber

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def cap(settings):
    settings.DAILY_EMAIL_CAP = 5


def msg():
    return EmailMessage("s", "b", to=["a@example.com"])


def fill(n, age=timedelta(hours=1)):
    for _ in range(n):
        EmailSend.objects.create(sent_at=NOW - age, kind="confirm")


def test_send_within_budget():
    assert send_budgeted(msg(), "confirm", NOW) is True
    assert EmailSend.objects.count() == 1
    assert len(mail.outbox) == 1


def test_over_budget_sends_nothing():
    fill(5)
    assert send_budgeted(msg(), "confirm", NOW) is False
    assert mail.outbox == []
    assert EmailSend.objects.count() == 5


def test_reserve_applies():
    fill(3)
    assert send_budgeted(msg(), "announce", NOW, reserve=2) is False
    assert send_budgeted(msg(), "announce", NOW, reserve=0) is True


def test_old_sends_dont_count():
    fill(5, age=timedelta(hours=25))
    assert sends_in_last_day(NOW) == 0
    assert send_budgeted(msg(), "confirm", NOW) is True


def test_record_send_prunes_old_rows():
    fill(2, age=timedelta(days=3))
    record_send("confirm", NOW)
    assert EmailSend.objects.count() == 1


def test_smtp_failure_propagates_and_is_not_counted(monkeypatch):
    def boom(self, *a, **k):
        raise OSError("smtp down")

    monkeypatch.setattr(EmailMessage, "send", boom)
    with pytest.raises(OSError):
        send_budgeted(msg(), "confirm", NOW)
    assert EmailSend.objects.count() == 0


def test_confirmation_email_content():
    sub = Subscriber.objects.create(email="a@example.com")
    assert send_confirmation(sub, NOW) is True
    sub.refresh_from_db()
    assert sub.confirm_sent_at == NOW
    (m,) = mail.outbox
    assert m.subject == "Confirm your UnitPricer waitlist signup"
    assert m.from_email == "UnitPricer <hello@unitpricer.com>"
    assert m.to == ["a@example.com"]
    assert "https://unitpricer.com/waitlist/confirm/" in m.body
    assert "/waitlist/unsubscribe/" in m.body
    assert "The link expires in 7 days." in m.body
    assert "If you didn't sign up, ignore this email and you won't hear from us again." in m.body
    unsub_url = m.body.split("Unsubscribe: ")[1].split()[0]
    assert m.extra_headers["List-Unsubscribe"] == f"<{unsub_url}>"
    assert m.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert not hasattr(m, "alternatives")


def test_confirmation_over_budget_leaves_confirm_sent_at_unset():
    fill(5)
    sub = Subscriber.objects.create(email="a@example.com")
    assert send_confirmation(sub, NOW) is False
    sub.refresh_from_db()
    assert sub.confirm_sent_at is None
