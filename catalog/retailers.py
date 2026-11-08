"""Retailer display names and which retailers currently have fresh offers."""

from __future__ import annotations

from datetime import datetime

from .freshness import fresh_observed, fresh_offers
from .models import Store

RETAILERS: dict[str, str] = {
    "walmart": "Walmart",
    "amazon": "Amazon",
    "target": "Target",
    "costco": "Costco",
    "shaws": "Shaw's",
    "stopandshop": "Stop & Shop",
}


def display_name(code: str) -> str:
    if code.startswith("op-"):
        chain = Store.objects.filter(chain_code=code).values_list("chain_name", flat=True).first()
        return f"{chain} (in-store prices)" if chain else code
    return RETAILERS.get(code, code.title())


def available_retailers(now: datetime) -> list[str]:
    def ordered(qs) -> list[str]:
        return sorted(set(qs.values_list("retailer", flat=True).distinct()), key=lambda c: (display_name(c), c))

    return ordered(fresh_offers(now)) + ordered(fresh_observed(now))
