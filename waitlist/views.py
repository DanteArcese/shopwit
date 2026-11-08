import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import IntegrityError
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from web.net import client_ip
from web.ratelimit import allow

from .mail import send_confirmation
from .models import Subscriber
from .tokens import read_confirm_token, read_unsubscribe_token

logger = logging.getLogger(__name__)

RESEND_WINDOW = timedelta(minutes=10)
_validate_email = EmailValidator()


def landing(request):
    return render(request, "waitlist/landing.html")


def privacy(request):
    return render(request, "waitlist/privacy.html")


def _clean_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if not email or len(email) > 254:
        return None
    try:
        _validate_email(email)
    except ValidationError:
        return None
    return email


@require_POST
def signup(request):
    if not allow("waitlist-ip", client_ip(request), 5, timedelta(hours=1)):
        return render(
            request,
            "waitlist/busy.html",
            {"rate_limited": True},
            status=429,
        )
    if request.POST.get("website"):
        return render(request, "waitlist/check_inbox.html")
    email = _clean_email(request.POST.get("email", ""))
    if email is None:
        return render(
            request, "waitlist/landing.html", {"error": "Enter a valid email address."}
        )

    now = timezone.now()
    Subscriber.purge_unconfirmed(now)
    existing = Subscriber.objects.filter(email=email).first()
    if existing is not None and existing.confirmed_at is not None:
        return render(request, "waitlist/check_inbox.html")
    recently_sent = (
        existing is not None
        and existing.confirm_sent_at is not None
        and existing.confirm_sent_at > now - RESEND_WINDOW
    )
    if recently_sent or not allow("waitlist-email", email, 3, timedelta(days=1)):
        return render(request, "waitlist/check_inbox.html")

    try:
        sub, created = Subscriber.objects.get_or_create(email=email)
    except IntegrityError:  # concurrent signup for the same address
        return render(request, "waitlist/check_inbox.html")
    try:
        sent = send_confirmation(sub, now)
    except Exception:
        logger.exception("waitlist confirmation send failed")
        sent = False
    if not sent and created:
        sub.delete()
        return render(
            request,
            "waitlist/busy.html",
            {},
            status=503,
        )
    return render(request, "waitlist/check_inbox.html")


@require_http_methods(["GET", "HEAD", "POST"])
def confirm(request, token):
    sub = read_confirm_token(token)
    if sub is None:
        return render(request, "waitlist/link_invalid.html")
    if request.method != "POST":
        return render(request, "waitlist/confirm.html")
    if sub.confirmed_at is None:
        sub.confirmed_at = timezone.now()
        sub.save(update_fields=["confirmed_at"])
    return render(request, "waitlist/confirmed.html")


@csrf_exempt  # RFC 8058 one-click POSTs from mail clients carry no CSRF token
@require_http_methods(["GET", "HEAD", "POST"])
def unsubscribe(request, token):
    valid, sub = read_unsubscribe_token(token)
    if not valid:
        return render(request, "waitlist/link_invalid.html", status=404)
    if request.method != "POST":
        return render(request, "waitlist/unsubscribe.html")
    if sub is not None:
        sub.delete()
    return render(request, "waitlist/unsubscribed.html")
