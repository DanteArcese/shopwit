from datetime import timedelta

from django.db import models

REPLY_EMAIL_MAX_AGE = timedelta(days=90)


class Feedback(models.Model):
    class Kind(models.TextChoices):
        STORE = "store", "Store request"
        DATA = "data", "Wrong price or size"
        BUG = "bug", "Bug"
        IDEA = "idea", "Idea"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        NEW = "new"
        APPROVED = "approved"
        REJECTED = "rejected"
        SYNCED = "synced"
        CLOSED = "closed"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    message = models.CharField(max_length=2000, blank=True)
    store_name = models.CharField(max_length=120, blank=True)
    website = models.CharField(max_length=200, blank=True)
    store_key = models.CharField(max_length=120, blank=True, db_index=True)
    offer = models.ForeignKey(
        "catalog.Offer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reply_email = models.EmailField(max_length=254, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.NEW, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    github_issue_number = models.PositiveIntegerField(null=True, blank=True)
    review_item = models.ForeignKey(
        "catalog.ReviewItem",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "feedback"

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk}"

    def save(self, *args, **kwargs):
        # The reply address lives only as long as the item is open.
        if (
            self.status in (self.Status.REJECTED, self.Status.CLOSED)
            and self.reply_email
        ):
            self.reply_email = ""
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "reply_email"}
        super().save(*args, **kwargs)


class StoreRequest(models.Model):
    """The single GitHub issue for one normalized store name."""

    store_key = models.CharField(max_length=120, unique=True)
    display_name = models.CharField(max_length=120)
    github_issue_number = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.display_name

    def approved_count(self) -> int:
        return Feedback.objects.filter(
            kind=Feedback.Kind.STORE,
            store_key=self.store_key,
            approved_at__isnull=False,
        ).count()
