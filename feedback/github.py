"""Minimal GitHub REST client for issues (create, update body, comment)."""

from __future__ import annotations

import httpx
from django.conf import settings

API = "https://api.github.com"
# Seconds per request; well under gunicorn's 30 s worker timeout.
TIMEOUT = 8
# Tests replace this with an httpx.MockTransport; None means the real network.
_transport: httpx.BaseTransport | None = None


class GitHubClient:
    def __init__(
        self, token: str, repo: str, transport: httpx.BaseTransport | None = None
    ):
        self.repo = repo
        self._http = httpx.Client(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "unitpricer-feedback",
            },
            timeout=TIMEOUT,
            transport=transport,
        )

    def _request(self, method: str, path: str, payload: dict) -> dict:
        response = self._http.request(method, f"/repos/{self.repo}{path}", json=payload)
        response.raise_for_status()
        return response.json()

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        return int(
            self._request(
                "POST", "/issues", {"title": title, "body": body, "labels": labels}
            )["number"]
        )

    def update_issue_body(self, number: int, body: str) -> None:
        self._request("PATCH", f"/issues/{number}", {"body": body})

    def add_comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": body})

    def close(self) -> None:
        self._http.close()


def make_client() -> GitHubClient | None:
    """A client from settings, or None when UNITPRICER_GITHUB_TOKEN is unset (sync disabled)."""
    if not settings.GITHUB_TOKEN:
        return None
    return GitHubClient(
        settings.GITHUB_TOKEN, settings.GITHUB_REPO, transport=_transport
    )


# What the admin action catches: transport/HTTP errors and malformed responses.
SYNC_ERRORS = (httpx.HTTPError, KeyError, ValueError, TypeError)


def describe(exc: Exception) -> str:
    """Short, secret-free description: "HTTP 401", or the exception type name."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__
