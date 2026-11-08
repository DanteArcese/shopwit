"""What admin approval does with each kind of feedback.

Bug, idea and other become one GitHub issue each; store requests share one
issue per normalized store name whose body carries the approved count; wrong
price or size reports never reach GitHub and open a catalog ReviewItem instead.
Nothing personal (reply email, IP) is ever sent.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from catalog.models import ReviewItem

from .github import GitHubClient
from .models import Feedback, StoreRequest
from .text import display_name, neutralize, quote

LABELS = {
    Feedback.Kind.BUG: "bug",
    Feedback.Kind.IDEA: "idea",
    Feedback.Kind.OTHER: "feedback",
}
TITLES = {
    Feedback.Kind.BUG: "Bug report (feedback #{pk})",
    Feedback.Kind.IDEA: "Idea (feedback #{pk})",
    Feedback.Kind.OTHER: "Feedback #{pk}",
}
FOOTER = "Submitted via unitpricer.com feedback."
USER_REPORT_DETAIL = "user report: wrong price or size"


def issue_body(item: Feedback) -> str:
    return f"{quote(neutralize(item.message))}\n\n{FOOTER}"


def store_body(count: int) -> str:
    times = "time" if count == 1 else "times"
    return f"Requested {count} {times} via unitpricer.com feedback.\n\n{FOOTER}"


def _mark_approved(item: Feedback, now: datetime) -> None:
    if item.approved_at is None:
        item.approved_at = now
    item.status = Feedback.Status.APPROVED
    item.save(update_fields=["approved_at", "status"])


def link_review_item(item: Feedback) -> ReviewItem | None:
    """Open (or join) the product's user-report ReviewItem; None without a product."""
    if item.offer is None:
        return None
    review, _ = ReviewItem.objects.get_or_create(
        product_id=item.offer.product_id,
        detail=USER_REPORT_DETAIL,
        status=ReviewItem.Status.OPEN,
        defaults={"reason": ReviewItem.Reason.USER_REPORT},
    )
    item.review_item = review
    item.save(update_fields=["review_item"])
    return review


def approve(
    item: Feedback, client: GitHubClient | None, now: datetime | None = None
) -> str:
    """Approve one item and sync it. Returns "review", "disabled" or "synced".

    Raises httpx errors from GitHub; the item then stays approved for a retry.
    """
    _mark_approved(item, now or timezone.now())
    if item.kind == Feedback.Kind.DATA:
        link_review_item(item)
        return "review"
    if client is None:
        return "disabled"
    if item.kind == Feedback.Kind.STORE:
        number = _sync_store(item, client)
    else:
        number = client.create_issue(
            TITLES[item.kind].format(pk=item.pk), issue_body(item), [LABELS[item.kind]]
        )
    item.github_issue_number = number
    item.status = Feedback.Status.SYNCED
    item.save(update_fields=["github_issue_number", "status"])
    return "synced"


def _sync_store(item: Feedback, client: GitHubClient) -> int:
    with transaction.atomic():
        request, _ = StoreRequest.objects.select_for_update().get_or_create(
            store_key=item.store_key,
            defaults={"display_name": display_name(item.store_name)},
        )
        body = store_body(request.approved_count())
        if request.github_issue_number is None:
            request.github_issue_number = client.create_issue(
                f"Store request: {request.display_name}", body, ["store-request"]
            )
            request.save(update_fields=["github_issue_number"])
        else:
            client.update_issue_body(request.github_issue_number, body)
        # Requests approved while sync was off are counted in the same issue.
        Feedback.objects.filter(
            kind=Feedback.Kind.STORE,
            store_key=item.store_key,
            status=Feedback.Status.APPROVED,
        ).exclude(pk=item.pk).update(
            status=Feedback.Status.SYNCED,
            github_issue_number=request.github_issue_number,
        )
        return request.github_issue_number


def merge(
    item: Feedback, client: GitHubClient, number: int, now: datetime | None = None
) -> None:
    """Add a +1 comment to an existing issue instead of opening a new one."""
    client.add_comment(number, f"+1 from another report:\n\n{issue_body(item)}")
    item.approved_at = item.approved_at or now or timezone.now()
    item.github_issue_number = number
    item.status = Feedback.Status.SYNCED
    item.save(update_fields=["approved_at", "github_issue_number", "status"])
