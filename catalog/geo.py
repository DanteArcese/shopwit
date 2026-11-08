"""Postal-code centroids and great-circle distances, offline.

Postal codes resolve through the bundled US Census ZCTA gazetteer
(data/geo/us_zcta.csv.gz, public domain). A code that is not in the file is
unknown: there is no fallback, no nearest match and no external lookup.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ZCTA_FILE = Path(__file__).resolve().parent.parent / "data" / "geo" / "us_zcta.csv.gz"
COUNTRIES = ("US",)
EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius
KM_PER_MILE = 1.609344

_US_POSTAL = re.compile(r"(\d{5})(?:-?\d{4})?")


@dataclass(frozen=True)
class Near:
    """A centre point and radius used to keep only nearby stores."""

    lat: float
    lon: float
    radius_km: float


def normalize_postal_code(raw: str | None, country: str = "US") -> str | None:
    """The canonical form of a postal code, or None when it is not well formed.

    US: five digits; a ZIP+4 ("94043-1234" or "940431234") keeps its first five.
    """
    if country not in COUNTRIES or not isinstance(raw, str):
        return None
    match = _US_POSTAL.fullmatch(raw.strip())
    return match.group(1) if match else None


@lru_cache(maxsize=1)
def _zcta() -> dict[str, tuple[float, float]]:
    with gzip.open(ZCTA_FILE, "rb") as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
        return {row["zip"]: (float(row["lat"]), float(row["lon"])) for row in reader}


def locate(postal_code: str | None, country: str = "US") -> tuple[float, float] | None:
    """(lat, lon) of the postal code's centroid, or None when unknown."""
    code = normalize_postal_code(postal_code, country)
    if code is None:
        return None
    return _zcta().get(code)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(near: Near) -> tuple[float, float, float | None, float | None]:
    """(min_lat, max_lat, min_lon, max_lon) containing every point within the radius.

    The longitude bounds are None when the box would reach a pole or cross the
    antimeridian; callers then filter on latitude only and rely on the exact
    distance check.
    """
    angular = near.radius_km / EARTH_RADIUS_KM
    dlat = math.degrees(angular) + 1e-9
    min_lat, max_lat = near.lat - dlat, near.lat + dlat
    if min_lat <= -90 or max_lat >= 90:
        return max(min_lat, -90.0), min(max_lat, 90.0), None, None
    # Exact longitude half-width of a spherical cap centred at near.lat, plus
    # a hair of slack so float rounding never drops a store on the boundary.
    dlon = math.degrees(math.asin(math.sin(angular) / math.cos(math.radians(near.lat)))) + 1e-9
    min_lon, max_lon = near.lon - dlon, near.lon + dlon
    if min_lon < -180 or max_lon > 180:
        return min_lat, max_lat, None, None
    return min_lat, max_lat, min_lon, max_lon


def within(near: Near, lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return haversine_km(near.lat, near.lon, lat, lon) <= near.radius_km
