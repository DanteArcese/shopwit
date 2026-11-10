import pytest
from django.test import RequestFactory

from web.prefs import Prefs, get_prefs, save_prefs


def make_request(accept_language=None, session=None):
    headers = {}
    if accept_language is not None:
        headers["HTTP_ACCEPT_LANGUAGE"] = accept_language
    request = RequestFactory().get("/", **headers)
    request.session = {} if session is None else session
    return request


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en-US", "imperial"),
        ("en-us,en;q=0.5", "imperial"),
        ("en-LR", "imperial"),
        ("my-MM", "imperial"),
        ("en-GB", "metric"),
        ("de-DE", "metric"),
        ("en", "imperial"),
        ("fr", "imperial"),
        ("fr-CA", "metric"),
        ("de-DE,en-US;q=0.8", "metric"),
        ("en-Latn-US", "imperial"),
        ("en_US", "imperial"),
        ("  en-US , de", "imperial"),
        ("en-419", "metric"),
        ("*", "imperial"),
        ("*;q=0.5", "imperial"),
        ("-US", "imperial"),
        ("", "imperial"),
        (None, "imperial"),
    ],
)
def test_default_unit_system_from_accept_language(header, expected):
    prefs = get_prefs(make_request(header))
    assert prefs == Prefs(unit_system=expected, stores=None)


def test_prefs_round_trip_in_session():
    request = make_request("en-US")
    save_prefs(request, Prefs("metric", frozenset({"walmart", "kroger"})))
    assert request.session["prefs"] == {
        "unit_system": "metric",
        "stores": ["kroger", "walmart"],
    }
    assert get_prefs(request) == Prefs("metric", frozenset({"walmart", "kroger"}))


def test_prefs_round_trip_all_stores():
    request = make_request()
    save_prefs(request, Prefs("imperial", None))
    assert request.session["prefs"] == {"unit_system": "imperial", "stores": None}
    assert get_prefs(request) == Prefs("imperial", None)


def test_invalid_session_prefs_fall_back_to_default():
    request = make_request("en-US", {"prefs": {"unit_system": "bogus", "stores": 5}})
    assert get_prefs(request) == Prefs("imperial", None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("junk", Prefs("imperial", None)),
        (["metric"], Prefs("imperial", None)),
        ({"unit_system": "metric", "stores": [1, 2]}, Prefs("metric", None)),
        ({"unit_system": "metric", "stores": "kroger"}, Prefs("metric", None)),
        ({"unit_system": "metric", "stores": []}, Prefs("metric", None)),
        ({"unit_system": ["x"], "stores": ["kroger"]}, Prefs("imperial", frozenset({"kroger"}))),
    ],
)
def test_tampered_session_prefs(raw, expected):
    request = make_request("en-US", {"prefs": raw})
    assert get_prefs(request) == expected
