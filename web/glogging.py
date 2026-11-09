import logging
import re

from gunicorn.glogging import Logger

_TOKEN_PATH = re.compile(r"(/waitlist/(?:confirm|unsubscribe)/|/account/magic/)[^\s/?]+")


def redact(text: str) -> str:
    return _TOKEN_PATH.sub("\\1\u2026", text)


class RedactingLogger(Logger):
    """Gunicorn logger that never writes waitlist or sign-in tokens to the access log."""

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in ("U", "r", "RU"):
            if key in atoms and isinstance(atoms[key], str):
                atoms[key] = redact(atoms[key])
        return atoms


class RedactFilter(logging.Filter):
    """Redact token paths from app log records (e.g. django.request's 'Internal Server Error: <path>')."""

    def filter(self, record):
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True
