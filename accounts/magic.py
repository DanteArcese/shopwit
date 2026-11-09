"""Magic-link sign-in tokens: 32 random bytes, stored only as SHA-256, 15 minutes, single use."""

import hashlib
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from waitlist.mail import send_budgeted

from .models import MagicLinkToken

SUBJECT = "Your UnitPricer sign-in link"
PURGE_AFTER = timedelta(days=1)
_MAX_RAW = 100  # token_urlsafe(32) is 43 characters


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _valid_raw(raw: str) -> bool:
    return bool(raw) and len(raw) <= _MAX_RAW


def issue_token(email: str, next_path: str, now: datetime) -> str:
    # Expired rows hold an email address; don't let them accumulate.
    MagicLinkToken.objects.filter(expires_at__lt=now - PURGE_AFTER).delete()
    raw = secrets.token_urlsafe(32)
    MagicLinkToken.objects.create(
        email=email,
        token_hash=_hash(raw),
        next_path=next_path[:500],
        expires_at=now + settings.MAGIC_LINK_TTL,
    )
    return raw


def peek_token(raw: str, now: datetime) -> MagicLinkToken | None:
    """The unused, unexpired token, without consuming it."""
    if not _valid_raw(raw):
        return None
    return MagicLinkToken.objects.filter(
        token_hash=_hash(raw), used_at__isnull=True, expires_at__gt=now
    ).first()


def consume_token(raw: str, now: datetime) -> MagicLinkToken | None:
    """Mark the token used in one conditional UPDATE; only the caller that changed it wins."""
    if not _valid_raw(raw):
        return None
    token_hash = _hash(raw)
    changed = MagicLinkToken.objects.filter(
        token_hash=token_hash, used_at__isnull=True, expires_at__gt=now
    ).update(used_at=now)
    if changed != 1:
        return None
    return MagicLinkToken.objects.get(token_hash=token_hash)


def _origin() -> str:
    if settings.SITE_URL:
        return settings.SITE_URL.rstrip("/")
    if settings.DEBUG:
        return "http://localhost:8000"
    return ""


def send_magic_link(email: str, next_path: str, now: datetime) -> bool:
    """Issue and mail a link through the shared daily email budget.

    Returns False when the budget is spent; the unsent token is deleted, as it
    is when sending raises (the exception propagates).
    """
    raw = issue_token(email, next_path, now)
    link = f"{_origin()}/account/magic/{raw}"
    message = EmailMessage(
        subject=SUBJECT,
        body=render_to_string("accounts/magic_link_email.txt", {"link": link}),
        to=[email],
    )
    try:
        sent = send_budgeted(message, kind="magic", now=now)
    except BaseException:
        MagicLinkToken.objects.filter(token_hash=_hash(raw)).delete()
        raise
    if not sent:
        MagicLinkToken.objects.filter(token_hash=_hash(raw)).delete()
    return sent
