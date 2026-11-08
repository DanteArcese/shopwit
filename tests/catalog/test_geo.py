import gzip
import math

import pytest

from catalog import geo
from catalog.geo import (
    Near,
    bounding_box,
    haversine_km,
    locate,
    normalize_postal_code,
    within,
)


def test_zcta_file_is_compact_and_well_formed():
    with gzip.open(geo.ZCTA_FILE, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines[0] == "zip,lat,lon"
    assert len(lines) - 1 > 30000
    assert geo.ZCTA_FILE.stat().st_size < 1_000_000


def test_locate_known_zip():
    # Census 2025 gazetteer INTPTLAT/INTPTLONG for ZCTA 94043 (Mountain View, CA).
    assert locate("94043") == (37.411913, -122.068919)
    assert locate(" 10001 ") == (40.750649, -73.997298)
    assert locate("94043-1351") == locate("94043")


@pytest.mark.parametrize("code", ["00000", "99999"])
def test_locate_unknown_zip_is_none(code):
    assert locate(code) is None


@pytest.mark.parametrize(
    "code", [None, "", "9404", "940433", "abcde", "94 043", "M5V 3L9"]
)
def test_locate_malformed_is_none(code):
    assert locate(code) is None


def test_locate_other_country_is_none():
    assert locate("94043", country="CA") is None


def test_lookup_is_cached():
    geo._zcta()
    before = geo._zcta.cache_info().hits
    locate("94043")
    locate("10001")
    assert geo._zcta.cache_info().hits == before + 2


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("94043", "94043"),
        (" 94043 ", "94043"),
        ("94043-1351", "94043"),
        ("940431351", "94043"),
        ("9404", None),
        ("94043-12", None),
        ("", None),
        (None, None),
        ("ninety", None),
    ],
)
def test_normalize_postal_code(raw, expected):
    assert normalize_postal_code(raw) == expected


def test_haversine_known_distances():
    # One degree of latitude along a meridian is 2*pi*R/360.
    assert haversine_km(0, 0, 1, 0) == pytest.approx(
        2 * math.pi * geo.EARTH_RADIUS_KM / 360
    )
    # Antipodes on the equator: half the circumference.
    assert haversine_km(0, 0, 0, 180) == pytest.approx(math.pi * geo.EARTH_RADIUS_KM)
    assert haversine_km(37.4, -122.0, 37.4, -122.0) == 0
    # ZCTA 94043 to ZCTA 10001: about 4,100 km.
    d = haversine_km(*locate("94043"), *locate("10001"))
    assert 4080 < d < 4140


def test_within_requires_coordinates():
    near = Near(37.4, -122.0, 40)
    assert within(near, 37.4, -122.0)
    assert not within(near, None, -122.0)
    assert not within(near, 37.4, None)
    assert not within(near, 40.0, -122.0)  # ~289 km north


def _destination(lat, lon, bearing_deg, km):
    """Point reached from (lat, lon) after km along the given initial bearing."""
    d = km / geo.EARTH_RADIUS_KM
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


@pytest.mark.parametrize("lat", [0.0, 37.4, 64.8, -45.0])
@pytest.mark.parametrize("radius_km", [16.0, 40.2336, 160.9344])
def test_bounding_box_contains_every_point_within_radius(lat, radius_km):
    near = Near(lat, -100.0, radius_km)
    min_lat, max_lat, min_lon, max_lon = bounding_box(near)
    for bearing in range(0, 360, 1):
        for frac in (0.5, 0.999999):
            p_lat, p_lon = _destination(lat, -100.0, bearing, radius_km * frac)
            assert within(near, p_lat, p_lon)
            assert min_lat <= p_lat <= max_lat
            assert min_lon <= p_lon <= max_lon
    # The box is tight: a point just beyond the radius due north is outside it.
    assert _destination(lat, -100.0, 0, radius_km * 1.001)[0] > max_lat


def test_bounding_box_drops_longitude_at_antimeridian():
    assert bounding_box(Near(52.0, 179.9, 50))[2:] == (None, None)
    assert bounding_box(Near(89.9, 0.0, 50))[2:] == (None, None)
