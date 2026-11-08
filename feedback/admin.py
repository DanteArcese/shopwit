import logging

import httpx
from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.template.response import TemplateResponse

from . import github, sync
from .models import Feedback, StoreRequest

logger = logging.getLogger(__name__)

DISABLED = "GitHub sync is disabled (UNITPRICER_GITHUB_TOKEN is unset)"
MERGEABLE = (Feedback.Kind.BUG, Feedback.Kind.IDEA, Feedback.Kind.OTHER)


class MergeForm(forms.Form):
    issue_number = forms.IntegerField(min_value=1, label="Existing issue number")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "kind",
        "status",
        "summary",
        "store_key",
        "github_issue_number",
        "created_at",
    )
    list_filter = ("status", "kind")
    search_fields = ("message", "store_name", "store_key")
    readonly_fields = (
        "kind",
        "status",
        "message",
        "store_name",
        "website",
        "store_key",
        "offer",
        "reply_email",
        "created_at",
        "approved_at",
        "github_issue_number",
        "review_item",
    )
    fields = readonly_fields
    actions = ["approve", "reject", "close", "merge_into_issue"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return obj is None  # list actions only; the change page is read-only

    @admin.display(description="text")
    def summary(self, obj):
        text = obj.store_name if obj.kind == Feedback.Kind.STORE else obj.message
        return text[:80]

    @admin.action(
        description="Approve and sync (GitHub issue, or review queue for wrong data)"
    )
    def approve(self, request, queryset):
        client = github.make_client()
        counts = {"synced": 0, "review": 0, "disabled": 0, "failed": 0, "no_product": 0}
        try:
            pending = queryset.filter(
                status__in=[Feedback.Status.NEW, Feedback.Status.APPROVED]
            )
            for item in pending.select_related("offer"):
                try:
                    outcome = sync.approve(item, client)
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    logger.error(
                        "feedback %s sync failed: %s", item.pk, type(exc).__name__
                    )
                    counts["failed"] += 1
                    continue
                if outcome == "review" and item.review_item_id is None:
                    outcome = "no_product"
                counts[outcome] += 1
        finally:
            if client is not None:
                client.close()
        if counts["synced"]:
            self.message_user(
                request,
                f"{counts['synced']} item(s) synced to GitHub.",
                messages.SUCCESS,
            )
        if counts["review"]:
            self.message_user(
                request,
                f"{counts['review']} item(s) added to the review queue.",
                messages.SUCCESS,
            )
        if counts["no_product"]:
            self.message_user(
                request,
                f"{counts['no_product']} item(s) approved with no product to review.",
                messages.WARNING,
            )
        if counts["disabled"]:
            self.message_user(
                request,
                f"{counts['disabled']} item(s) approved; {DISABLED}.",
                messages.WARNING,
            )
        if counts["failed"]:
            self.message_user(
                request,
                f"{counts['failed']} item(s) approved but GitHub sync failed; approve again to retry.",
                messages.ERROR,
            )

    @admin.action(description="Reject (deletes the reply email)")
    def reject(self, request, queryset):
        n = self._set_status(
            queryset.exclude(status=Feedback.Status.SYNCED), Feedback.Status.REJECTED
        )
        self.message_user(request, f"{n} item(s) rejected.", messages.SUCCESS)

    @admin.action(description="Close (deletes the reply email)")
    def close(self, request, queryset):
        n = self._set_status(queryset, Feedback.Status.CLOSED)
        self.message_user(request, f"{n} item(s) closed.", messages.SUCCESS)

    def _set_status(self, queryset, status):
        n = 0
        for item in queryset.exclude(status=status):
            item.status = status
            item.save(update_fields=["status"])  # save() also blanks reply_email
            n += 1
        return n

    @admin.action(description="Merge into an existing GitHub issue (+1 comment)")
    def merge_into_issue(self, request, queryset):
        items = list(
            queryset.filter(kind__in=MERGEABLE).exclude(status=Feedback.Status.SYNCED)
        )
        if not items:
            self.message_user(
                request,
                "Only unsynced bug, idea or other items can be merged.",
                messages.WARNING,
            )
            return None
        form = MergeForm(request.POST if "apply" in request.POST else None)
        if not form.is_valid():
            return TemplateResponse(
                request,
                "admin/feedback/feedback/merge.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Merge into an existing issue",
                    "items": items,
                    "form": form,
                    "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                    "opts": self.model._meta,
                },
            )
        client = github.make_client()
        if client is None:
            self.message_user(request, f"Nothing merged: {DISABLED}.", messages.WARNING)
            return None
        number = form.cleaned_data["issue_number"]
        merged = failed = 0
        try:
            for item in items:
                try:
                    sync.merge(item, client, number)
                    merged += 1
                except httpx.HTTPError as exc:
                    logger.error(
                        "feedback %s merge failed: %s", item.pk, type(exc).__name__
                    )
                    failed += 1
        finally:
            client.close()
        if merged:
            self.message_user(
                request, f"{merged} item(s) merged into #{number}.", messages.SUCCESS
            )
        if failed:
            self.message_user(
                request,
                f"{failed} item(s) could not be merged into #{number}.",
                messages.ERROR,
            )
        return None


@admin.register(StoreRequest)
class StoreRequestAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "store_key",
        "approved",
        "github_issue_number",
        "created_at",
    )
    search_fields = ("display_name", "store_key")
    readonly_fields = ("display_name", "store_key", "github_issue_number", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return obj is None

    @admin.display(description="approved requests")
    def approved(self, obj):
        return obj.approved_count()
