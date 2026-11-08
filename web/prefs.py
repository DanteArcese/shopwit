"""Per-visitor display preferences, kept in the server-side session (no accounts).

Browsing sets no cookies; saving settings creates the session and CSRF cookies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

UnitSystem = Literal["imperial", "metric"]

IMPERIAL_REGIONS = frozenset({"US", "LR", "MM"})


@dataclass(frozen=True)
class Prefs:
    unit_system: UnitSystem
    stores: frozenset[str] | None = None  # None means all retailers


def default_unit_system(accept_language: str) -> UnitSystem:
    """Imperial iff the first tag's region (2 letters or 3 digits) is US, LR or MM."""
    first = accept_language.split(",", 1)[0].split(";", 1)[0].strip()
    language, *subtags = first.replace("_", "-").split("-")
    if not language or language == "*":
        return "metric"
    for tag in subtags:
        if (len(tag) == 2 and tag.isascii() and tag.isalpha()) or (
            len(tag) == 3 and tag.isascii() and tag.isdigit()
        ):
            return "imperial" if tag.upper() in IMPERIAL_REGIONS else "metric"
    return "metric"


def get_prefs(request) -> Prefs:
    raw = request.session.get("prefs")
    default = default_unit_system(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))
    if not isinstance(raw, dict):
        return Prefs(default, None)
    unit_system = raw.get("unit_system")
    if unit_system not in ("imperial", "metric"):
        unit_system = default
    stores = raw.get("stores")
    if isinstance(stores, list) and stores and all(isinstance(s, str) for s in stores):
        return Prefs(unit_system, frozenset(stores))
    return Prefs(unit_system, None)


def save_prefs(request, prefs: Prefs) -> None:
    request.session["prefs"] = {
        "unit_system": prefs.unit_system,
        "stores": None if prefs.stores is None else sorted(prefs.stores),
    }


def split_stores(stores: frozenset[str] | None) -> tuple[frozenset[str] | None, frozenset[str] | None]:
    """Split a store selection into (live retailers, op- chains) filters.

    Live codes filter only the live ranking; op- chain codes filter only the
    "Seen in stores" section. A side with nothing selected is unfiltered (None),
    so choosing only chains never empties the live ranking and choosing only
    live stores never empties the observed section.
    """
    if stores is None:
        return None, None
    chains = frozenset(c for c in stores if c.startswith("op-"))
    live = frozenset(stores - chains)
    return (live or None), (chains or None)
