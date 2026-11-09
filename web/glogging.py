import logging
import re

from gunicorn.glogging import Logger

_TOKEN_PATH = re.compile(r"(/waitlist/(?:confirm|unsubscribe)/|/account/magic/)[^\s/?'\"]+")


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


_formatter = logging.Formatter()


class RedactFilter(logging.Filter):
    """Redact token paths from app log records: message, traceback and stack text.

    Never raises and never drops a record.
    """

    def filter(self, record):
        try:
            message = record.getMessage()
            redacted = redact(message)
            if redacted != message:
                record.msg, record.args = redacted, None
        except Exception:
            pass
        try:
            if record.exc_info and not record.exc_text:
                # The formatter reuses exc_text, so the redacted text is what's written.
                record.exc_text = _formatter.formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            pass
        return True
