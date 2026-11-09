"""Per-visitor display preferences, kept in the server-side session (no accounts).

Browsing sets no cookies; saving settings creates the session and CSRF cookies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from catalog.geo import KM_PER_MILE, normalize_postal_code

UnitSystem = Literal["imperial", "metric"]

IMPERIAL_REGIONS = frozenset({"US", "LR", "MM"})

# Radius steps, stored by their size in miles. Metric viewers see (and are
# filtered by) the round kilometre figure for the same step, so the distance
# shown is always the distance used.
RADIUS_STEPS = (10, 25, 50, 100)
DEFAULT_RADIUS = 25
METRIC_RADIUS_KM = {10: 16, 25: 40, 50: 80, 100: 160}
DEFAULT_COUNTRY = "US"


@dataclass(frozen=True)
class Prefs:
    unit_system: UnitSystem
    stores: frozenset[str] | None = None  # None means all retailers
    postal_code: str | None = None  # normalized; never logged or put in URLs
    country: str = DEFAULT_COUNTRY
    radius: int = DEFAULT_RADIUS  # one of RADIUS_STEPS
    comparisons: Mapping[str, str] = field(default_factory=dict)  # category slug -> comparison


def radius_km(radius: int, unit_system: UnitSystem) -> float:
    if unit_system == "metric":
        return float(METRIC_RADIUS_KM[radius])
    return radius * KM_PER_MILE


def radius_label(radius: int, unit_system: UnitSystem) -> str:
    if unit_system == "metric":
        return f"{METRIC_RADIUS_KM[radius]} km"
    return f"{radius} mi"


def radius_choices(unit_system: UnitSystem) -> list[tuple[int, str]]:
    return [(step, radius_label(step, unit_system)) for step in RADIUS_STEPS]


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


def clean_comparisons(raw) -> dict[str, str]:
    """A str -> str mapping; anything else is ignored."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def parse_prefs(raw, default: UnitSystem) -> Prefs:
    """Prefs from a stored dict (session or UserSettings); bad values fall back."""
    if not isinstance(raw, dict):
        return Prefs(default, None)
    unit_system = raw.get("unit_system")
    if unit_system not in ("imperial", "metric"):
        unit_system = default
    stores = raw.get("stores")
    if not (isinstance(stores, list) and stores and all(isinstance(s, str) for s in stores)):
        stores = None
    country = raw.get("country", DEFAULT_COUNTRY)
    postal_code = normalize_postal_code(raw.get("postal_code"), country)
    radius = raw.get("radius", DEFAULT_RADIUS)
    if isinstance(radius, bool) or radius not in RADIUS_STEPS:
        radius = DEFAULT_RADIUS
    return Prefs(
        unit_system,
        None if stores is None else frozenset(stores),
        postal_code=postal_code,
        country=country if postal_code else DEFAULT_COUNTRY,
        radius=radius,
        comparisons=clean_comparisons(raw.get("comparisons")),
    )


def _account_user(request):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def get_prefs(request) -> Prefs:
    default = default_unit_system(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))
    user = _account_user(request)
    if user is not None:
        from accounts.models import UserSettings

        row = UserSettings.objects.filter(user=user).first()
        if row is None:
            return Prefs(default, None)
        return parse_prefs(
            {
                "unit_system": row.unit_system,
                "stores": row.stores,
                "postal_code": row.postal_code,
                "country": row.country,
                "radius": row.radius,
                "comparisons": row.comparisons,
            },
            default,
        )
    return parse_prefs(request.session.get("prefs"), default)


def save_prefs(request, prefs: Prefs) -> None:
    stores = None if prefs.stores is None else sorted(prefs.stores)
    comparisons = clean_comparisons(dict(prefs.comparisons))
    user = _account_user(request)
    if user is not None:
        from accounts.models import UserSettings

        UserSettings.objects.update_or_create(
            user=user,
            defaults={
                "unit_system": prefs.unit_system,
                "stores": stores,
                "postal_code": prefs.postal_code or "",
                "country": prefs.country,
                "radius": prefs.radius,
                "comparisons": comparisons,
            },
        )
        return
    data = {"unit_system": prefs.unit_system, "stores": stores}
    # Optional keys only when set, so the session holds nothing it does not need.
    if prefs.postal_code:
        data["postal_code"] = prefs.postal_code
        data["country"] = prefs.country
    if prefs.radius != DEFAULT_RADIUS:
        data["radius"] = prefs.radius
    if comparisons:
        data["comparisons"] = comparisons
    request.session["prefs"] = data


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
