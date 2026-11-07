"""Recorded-response harness: serve saved API responses via httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import httpx

REDACTED_PARAMS = {"publisherId", "apiKey", "key", "secret"}


def _safe(text: str) -> str:
    # Keep unreserved characters; percent-encode everything else ("/", " ", "%", "&", "=").
    return quote(text, safe="")


def recording_name(request: httpx.Request) -> str:
    """File name for a request's recording.

    ``<METHOD>_<path>[__<query>].json`` where the path has its leading slash
    stripped and remaining "/" replaced by "_". The query is the sorted
    ``k=v`` pairs joined with "&", with REDACTED_PARAMS dropped. Keys, values
    and path segments are percent-encoded (RFC 3986 unreserved characters
    kept), so spaces, "%", "/", "&" and "=" inside them are made safe
    deterministically. No query (after redaction) means no "__" suffix.
    """
    path = "_".join(_safe(seg) for seg in request.url.path.lstrip("/").split("/"))
    pairs = sorted(
        (k, v) for k, v in request.url.params.multi_items() if k not in REDACTED_PARAMS
    )
    name = f"{request.method}_{path}"
    if pairs:
        name += "__" + "&".join(f"{_safe(k)}={_safe(v)}" for k, v in pairs)
    return name + ".json"


def recorded_transport(directory: Path) -> httpx.MockTransport:
    """Transport serving ``{"status": int, "json": ...}`` files from directory."""
    directory = Path(directory)

    def handler(request: httpx.Request) -> httpx.Response:
        expected = directory / recording_name(request)
        if not expected.is_file():
            raise AssertionError(f"No recording for request; expected file: {expected}")
        data = json.loads(expected.read_text())
        return httpx.Response(data["status"], json=data["json"])

    return httpx.MockTransport(handler)
