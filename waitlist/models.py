from django.conf import settings
from django.db import models


class Subscriber(models.Model):
    """A waitlist signup. Stores only the lowercased email and lifecycle timestamps."""

    email = models.EmailField(max_length=254, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    confirm_sent_at = models.DateTimeField(null=True)
    confirmed_at = models.DateTimeField(null=True, db_index=True)
    announced_at = models.DateTimeField(null=True)

    def __str__(self):
        return self.email

    @classmethod
    def purge_unconfirmed(cls, now) -> int:
        """Delete unconfirmed rows older than the confirm-token lifetime."""
        cutoff = now - settings.WAITLIST_CONFIRM_MAX_AGE
        deleted, _ = cls.objects.filter(confirmed_at__isnull=True, created_at__lt=cutoff).delete()
        return deleted


class EmailSend(models.Model):
    """One row per email sent, for the rolling daily budget. Holds no address."""

    sent_at = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=16)  # "confirm", "announce" (M4: "magic")
