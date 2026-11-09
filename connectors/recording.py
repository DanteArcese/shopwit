"""Recorded-response harness: serve saved API responses via httpx.MockTransport."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx

MAX_NAME = 255  # file systems cap a name at 255 bytes


REDACTED_PARAMS = {
    "publisherid",
    "apikey",
    "api_key",
    "access_token",
    "token",
    "key",
    "secret",
}  # lowercase


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
    deterministically. No query (after redaction) means no "__" suffix. A name
    longer than MAX_NAME keeps its first MAX_NAME - 46 characters, then ``~`` and
    the SHA-1 of the full name, so it still fits a file system.
    """
    path = "_".join(_safe(seg) for seg in request.url.path.lstrip("/").split("/"))
    pairs = sorted(
        (k, v)
        for k, v in request.url.params.multi_items()
        if k.lower() not in REDACTED_PARAMS
    )
    name = f"{request.method}_{path}"
    if pairs:
        name += "__" + "&".join(f"{_safe(k)}={_safe(v)}" for k, v in pairs)
    name += ".json"
    if len(name) > MAX_NAME:
        digest = hashlib.sha1(name.encode()).hexdigest()
        name = f"{name[: MAX_NAME - 46]}~{digest}.json"
    return name


def recorded_transport(directory: Path, *, strict: bool = False) -> httpx.MockTransport:
    """Transport serving ``{"status": int, "json": ...}`` files from directory.

    A missing recording answers 404 with a JSON body (connectors turn that into
    a ConnectorError). With ``strict=True`` it raises AssertionError naming the
    expected file instead, so tests notice unexpected requests.
    """
    directory = Path(directory)

    def handler(request: httpx.Request) -> httpx.Response:
        expected = directory / recording_name(request)
        if not expected.is_file():
            if strict:
                raise AssertionError(f"No recording for request; expected file: {expected}")
            return httpx.Response(404, json={"error": "no recording"})
        data = json.loads(expected.read_text())
        return httpx.Response(data["status"], json=data["json"], headers=data.get("headers"))

    return httpx.MockTransport(handler)
