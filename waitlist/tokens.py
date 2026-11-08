from django.conf import settings
from django.core import signing

from .models import Subscriber

CONFIRM_SALT = "waitlist.confirm"
UNSUBSCRIBE_SALT = "waitlist.unsubscribe"


def _payload(sub: Subscriber) -> dict:
    return {"id": sub.pk, "e": sub.email}


def _lookup(data) -> Subscriber | None:
    try:
        return Subscriber.objects.filter(pk=data["id"], email=data["e"]).first()
    except (KeyError, TypeError, ValueError):
        return None


def confirm_token(sub: Subscriber) -> str:
    return signing.dumps(_payload(sub), salt=CONFIRM_SALT, compress=True)


def read_confirm_token(token: str) -> Subscriber | None:
    try:
        data = signing.loads(
            token,
            salt=CONFIRM_SALT,
            max_age=settings.WAITLIST_CONFIRM_MAX_AGE.total_seconds(),
        )
    except signing.BadSignature:  # includes SignatureExpired
        return None
    return _lookup(data)


def unsubscribe_token(sub: Subscriber) -> str:
    return signing.dumps(_payload(sub), salt=UNSUBSCRIBE_SALT, compress=True)


def read_unsubscribe_token(token: str) -> tuple[bool, Subscriber | None]:
    """Return (signature_valid, subscriber). No expiry."""
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return False, None
    return True, _lookup(data)
