"""Live text search: the protocol a source implements and the budget it draws from.

No Django import: the DB-backed budget lives in ``catalog.budget``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # type-only: connectors.open_prices imports retry_after from here
    import httpx

    from connectors.open_prices import Observation
    from enrichment.off import OffRecord
    from normalize.types import RawOffer


class BudgetExhausted(Exception):
    """No token now; ``wait`` is the number of seconds until one may be available."""

    def __init__(self, wait: float) -> None:
        super().__init__(wait)
        self.wait = float(wait)


class Stopping(BudgetExhausted):
    """The worker is shutting down: make no new request. Runs record it as "interrupted"."""

    def __init__(self) -> None:
        super().__init__(0.0)


def retry_after(resp: httpx.Response) -> float | None:
    """A Retry-After given in seconds; anything else (an HTTP date, garbage) is None."""
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class Budget(Protocol):
    def acquire(self, key: str) -> None:
        """Take one token for ``key`` or raise ``BudgetExhausted``."""

    def report(self, key: str, status: int | None, retry_after: float | None = None) -> None:
        """Feed back a response status (``None`` for a transport error)."""


@dataclass(frozen=True)
class LiveSearchResult:
    offers: list[RawOffer] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    off_records: list[OffRecord] = field(default_factory=list)
    skipped: int = 0


@dataclass(frozen=True)
class SourceInfo:
    """What the web process needs to know about a live source, without building it."""

    name: str
    budget_keys: tuple[str, ...]
    refresh_after: timedelta


class LiveSearchSource(Protocol):
    name: str  # keys FetchJob.source
    budget_keys: tuple[str, ...]  # the SourceBudget rows it draws from
    max_cache_age: timedelta
    refresh_after: timedelta

    def live_search(self, phrase: str, limit: int, budget: Budget) -> LiveSearchResult: ...

    def close(self) -> None: ...
