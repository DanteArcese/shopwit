"""Connector interface shared by all retailer connectors (no Django import)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from normalize.types import RawOffer


class ConnectorError(Exception):
    """HTTP or auth failure. Messages never contain credentials or full URLs
    with query strings."""


@dataclass(frozen=True)
class SearchResult:
    offers: list[RawOffer]
    skipped: int


class Connector(Protocol):
    name: str
    max_cache_age: timedelta
    records_price_history: bool

    def search(self, query: str, limit: int = 25) -> SearchResult: ...
