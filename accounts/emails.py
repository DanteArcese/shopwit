from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

MAX_LENGTH = 150  # User.username
_validate = EmailValidator()


def normalize_email(raw: str | None) -> str | None:
    """Stripped, lowercased and valid, at most 150 characters; else None."""
    email = (raw or "").strip().lower()
    if not email or len(email) > MAX_LENGTH:
        return None
    try:
        _validate(email)
    except ValidationError:
        return None
    return email
