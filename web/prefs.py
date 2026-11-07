"""Per-visitor display preferences, kept in the session (no accounts, no cookies of our own)."""

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
    """Imperial iff the first language tag's region is US, LR or MM."""
    first = accept_language.split(",", 1)[0].split(";", 1)[0].strip()
    _, sep, region = first.partition("-")
    if sep and region.upper() in IMPERIAL_REGIONS:
        return "imperial"
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
    if isinstance(stores, list) and all(isinstance(s, str) for s in stores):
        return Prefs(unit_system, frozenset(stores))
    return Prefs(unit_system, None)


def save_prefs(request, prefs: Prefs) -> None:
    request.session["prefs"] = {
        "unit_system": prefs.unit_system,
        "stores": None if prefs.stores is None else sorted(prefs.stores),
    }
