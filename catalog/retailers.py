"""Retailer display names and which retailers currently have fresh offers."""

from __future__ import annotations

from datetime import datetime

from django.db.models import Count

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


def is_chain_code(code: str) -> bool:
    return code.startswith("op-")


def chain_names(codes) -> dict[str, str]:
    """Chain display names for op- codes, in one query."""
    wanted = {c for c in codes if is_chain_code(c)}
    if not wanted:
        return {}
    rows = (
        Store.objects.filter(chain_code__in=wanted)
        .values("chain_code", "chain_name")
        .annotate(n=Count("pk"))
    )
    # One chain per code (casefolded slug): show its most common spelling, ties by name.
    best: dict[str, tuple[int, str]] = {}
    for r in rows:
        cand = (-r["n"], r["chain_name"])
        if r["chain_code"] not in best or cand < best[r["chain_code"]]:
            best[r["chain_code"]] = cand
    return {code: name for code, (_, name) in best.items()}


def display_name(code: str, chains: dict[str, str] | None = None) -> str:
    """Pass `chains` (from chain_names) to avoid a query per op- code."""
    if is_chain_code(code):
        if chains is None:
            chains = chain_names([code])
        chain = chains.get(code)
        return f"{chain} (in-store prices)" if chain else code
    return RETAILERS.get(code, code.title())


def available_retailers(now: datetime, category=None) -> list[str]:
    """Retailers with fresh offers; with a category, in-store chains are limited to it."""
    live = set(fresh_offers(now).values_list("retailer", flat=True).distinct())
    observed_qs = fresh_observed(now)
    if category is not None:
        # The offer's own category too: a generic offer on a curated product is not on the page.
        observed_qs = observed_qs.filter(product__category=category, category=category)
    observed = set(observed_qs.values_list("retailer", flat=True).distinct())
    chains = chain_names(observed)

    def ordered(codes) -> list[str]:
        return sorted(codes, key=lambda c: (display_name(c, chains), c))

    return ordered(live) + ordered(observed)
