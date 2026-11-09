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
