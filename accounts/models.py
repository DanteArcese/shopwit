from django.conf import settings
from django.db import models


class MagicLinkToken(models.Model):
    """A one-time sign-in link. Only the SHA-256 of the token is stored."""

    email = models.EmailField(max_length=150)
    token_hash = models.CharField(max_length=64, unique=True)
    next_path = models.CharField(max_length=500, default="/lists/")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"MagicLinkToken {self.pk}"


class UserSettings(models.Model):
    """An account's saved display settings (what web.prefs keeps in the session)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="unitpricer_settings"
    )
    unit_system = models.CharField(max_length=8, blank=True)  # blank: follow Accept-Language
    stores = models.JSONField(null=True, blank=True)  # None: all stores
    # Sensitive-ish: never logged, never put in a URL.
    postal_code = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=2, default="US")
    radius = models.PositiveSmallIntegerField(default=25)
    comparisons = models.JSONField(default=dict)  # category slug -> comparison name
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"UserSettings {self.pk}"
