import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import IntegrityError
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from web.hosts import normalize_host
from web.net import client_ip
from web.ratelimit import allow

from .mail import send_confirmation, sends_in_last_day
from .models import Subscriber
from .tokens import read_confirm_token, read_unsubscribe_token

logger = logging.getLogger(__name__)

RESEND_WINDOW = timedelta(minutes=10)
_validate_email = EmailValidator()


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


def _same_origin(request) -> bool:
    """The form carries no CSRF token (browsing sets no cookies), so a cross-site POST is stopped here.

    A browser form post sends Origin (or at least Referer). A foreign or "null"
    value is rejected; a request with neither header is a non-browser client,
    which cannot be tricked into acting for someone else.
    """
    host = normalize_host(request)
    allowed = {f"https://{host}"}
    if settings.DEBUG:
        allowed |= {f"http://{host}", f"http://{request.get_host()}"}
    origin = request.headers.get("Origin")
    if origin is not None:
        return origin in allowed
    referer = request.headers.get("Referer")
    if referer:
        parts = urlsplit(referer)
        return f"{parts.scheme}://{parts.netloc}" in allowed
    return True


@csrf_exempt  # the home-page form has no token; _same_origin replaces it
@require_POST
def signup(request):
    if not _same_origin(request):
        return HttpResponseForbidden()
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
            request, "waitlist/notify.html", {"error": "Enter a valid email address."}
        )

    now = timezone.now()
    if sends_in_last_day(now) >= settings.DAILY_EMAIL_CAP:
        # Same answer for every valid address, so budget state reveals nothing.
        return render(request, "waitlist/busy.html", {}, status=503)
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
    except Exception as exc:
        logger.error("waitlist confirmation send failed: %s", type(exc).__name__)
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
