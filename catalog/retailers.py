"""Retailer display names and which retailers currently have fresh offers."""

from __future__ import annotations

from datetime import datetime

from .freshness import fresh_offers

RETAILERS: dict[str, str] = {
    "walmart": "Walmart",
    "amazon": "Amazon",
    "target": "Target",
    "costco": "Costco",
    "shaws": "Shaw's",
    "stopandshop": "Stop & Shop",
}


def display_name(code: str) -> str:
    return RETAILERS.get(code, code.title())


def available_retailers(now: datetime) -> list[str]:
    codes = fresh_offers(now).values_list("retailer", flat=True).distinct()
    return sorted(set(codes), key=lambda c: (display_name(c), c))
