"""Core data types shared across the normalizer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Source = Literal["api", "off", "rule", "manual"]


@dataclass(frozen=True)
class RawOffer:
    retailer: str
    retailer_item_id: str
    title: str
    price: Decimal
    currency: str
    url: str
    fetched_at: datetime
    upc: str | None = None
    brand: str | None = None
    size_text: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OffProduct:
    """Open Food Facts product data; labels are OFF tags such as "en:organic"."""

    labels: frozenset[str]
    quantity: str | None = None


@dataclass(frozen=True)
class AttrValue:
    value: str | int | bool
    source: Source


@dataclass(frozen=True)
class NormalizedOffer:
    raw: RawOffer
    category: str | None
    attributes: dict[str, AttrValue]
    measures: dict[str, float]
    unit_prices: dict[str, Decimal]
    unresolved: tuple[str, ...]
