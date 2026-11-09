import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import connection as db_connection
from django.db import transaction
from django.template.loader import render_to_string

from .models import EmailSend, Subscriber
from .tokens import confirm_token, unsubscribe_token

logger = logging.getLogger(__name__)

DAY = timedelta(days=1)
# Arbitrary constant key for the Postgres advisory lock that serializes budget checks.
_BUDGET_LOCK_KEY = 0x554E5052  # "UNPR"


def sends_in_last_day(now) -> int:
    return EmailSend.objects.filter(sent_at__gt=now - DAY).count()


def record_send(kind: str, now) -> None:
    EmailSend.objects.create(sent_at=now, kind=kind)
    EmailSend.objects.filter(sent_at__lt=now - 2 * DAY).delete()


def send_budgeted(message: EmailMessage, kind: str, now, reserve: int = 0, connection=None) -> bool:
    """Send only while under DAILY_EMAIL_CAP - reserve in the last 24 h.

    Count, record and send run in one transaction behind a transaction-scoped
    Postgres advisory lock, so concurrent senders cannot both pass the check.
    The record is written before the send, so an SMTP exception rolls it back
    and propagates. Residual race: if the process dies after SMTP accepts the
    message but before COMMIT, that send goes uncounted (it errs toward
    under-counting by at most one per crash). The lock is held during the SMTP
    call, which serializes sends; fine at this volume.
    """
    with transaction.atomic():
        with db_connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [_BUDGET_LOCK_KEY])
        if sends_in_last_day(now) >= settings.DAILY_EMAIL_CAP - reserve:
            logger.warning("Daily email budget reached; %s email not sent", kind)
            return False
        record_send(kind, now)
        if connection is not None:
            message.connection = connection
        message.send()
    return True


def _origin() -> str:
    if settings.PUBLIC_URL:
        return settings.PUBLIC_URL.rstrip("/")
    if settings.DEBUG:
        return "http://localhost:8000"
    return ""


def waitlist_message(sub: Subscriber, subject: str, template: str, context: dict) -> EmailMessage:
    unsubscribe_url = f"{_origin()}/waitlist/unsubscribe/{unsubscribe_token(sub)}"
    body = render_to_string(template, context | {"unsubscribe_url": unsubscribe_url})
    return EmailMessage(
        subject=subject,
        body=body,
        to=[sub.email],
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


def send_confirmation(sub: Subscriber, now) -> bool:
    confirm_url = f"{_origin()}/waitlist/confirm/{confirm_token(sub)}"
    message = waitlist_message(
        sub,
        "Confirm your UnitPricer store announcements signup",
        "waitlist/email/confirm.txt",
        {"confirm_url": confirm_url},
    )
    if not send_budgeted(message, "confirm", now):
        return False
    sub.confirm_sent_at = now
    sub.save(update_fields=["confirm_sent_at"])
    return True
