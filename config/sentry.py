"""Optional Sentry error reporting that never sends personal data.

Off unless SENTRY_DSN is set. What leaves the server is documented on the privacy
page (waitlist/templates/waitlist/privacy.html); keep the two in step.
"""

import logging
import re

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from web.glogging import redact

log = logging.getLogger(__name__)

# Only these request headers are kept; everything else (Cookie, Authorization,
# Cf-Access-Jwt-Assertion, Cf-Connecting-Ip, X-Forwarded-For, Referer, ...) is dropped.
_KEEP_HEADERS = {"user-agent"}
_EMAIL = re.compile(r"[^\s@<>\"'(),;:]+@[^\s@<>\"'(),;]+\.[A-Za-z0-9-]{2,}")
_URL_QUERY = re.compile(r"(https?://[^\s?#'\"]*)[?#][^\s'\"]*")
# US ZIP codes (12345 or 12345-6789) as standalone tokens: the saved ZIP never leaves.
_ZIP = re.compile(r"(?<![\w.-])\d{5}(?:-\d{4})?(?![\w-])")
_DROP_KEYS = {"vars", "query_string", "cookies", "http.query", "http.fragment", "query", "fragment"}
_REQUEST_DROP = {"query_string", "cookies", "data", "env"}


def _scrub_str(text: str) -> str:
    return _ZIP.sub("[zip]", _EMAIL.sub("[email]", redact(_URL_QUERY.sub(r"\1", text))))


def _scrub_value(value):
    if isinstance(value, str):
        return _scrub_str(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items() if k not in _DROP_KEYS}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


def _scrub_headers(headers) -> dict:
    items = headers.items() if isinstance(headers, dict) else headers
    return {k: _scrub_str(str(v)) for k, v in items if str(k).lower() in _KEEP_HEADERS}


def scrub_event(event: dict, hint: dict) -> dict | None:
    """before_send hook. Drops the event rather than risk sending unscrubbed data."""
    try:
        event.pop("user", None)
        request = event.get("request")
        if isinstance(request, dict):
            for key in _REQUEST_DROP:
                request.pop(key, None)
            request["headers"] = _scrub_headers(request.get("headers") or {})
        if "server_name" in event:
            event["server_name"] = ""
        return _scrub_value(event)
    except Exception:
        return None


def init_sentry(dsn: str | None, environment: str = "production") -> bool:
    if not dsn:
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[DjangoIntegration()],
            environment=environment,
            send_default_pii=False,
            traces_sample_rate=0,
            include_local_variables=False,
            max_request_body_size="never",
            server_name="",
            before_send=scrub_event,
        )
    except Exception as exc:
        # A bad SENTRY_DSN must not take the site down. Never log the DSN (it holds the key)
        # or the exception text (which may quote it).
        log.warning("Sentry disabled: SENTRY_DSN could not be used (%s)", type(exc).__name__)
        return False
    return True
