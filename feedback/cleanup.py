from datetime import datetime

from django.db.models import Q

from .models import REPLY_EMAIL_MAX_AGE, Feedback


def purge_reply_emails(now: datetime) -> int:
    """Blank reply emails of closed or rejected items and of items older than 90 days."""
    stale = Q(status__in=[Feedback.Status.CLOSED, Feedback.Status.REJECTED]) | Q(
        created_at__lt=now - REPLY_EMAIL_MAX_AGE
    )
    return Feedback.objects.exclude(reply_email="").filter(stale).update(reply_email="")
