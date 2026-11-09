import json

import httpx
import pytest

from connectors.recording import recorded_transport, recording_name


def _client(directory, **kw):
    return httpx.Client(
        transport=recorded_transport(directory, **kw), base_url="https://api.example.test"
    )


def test_recorded_transport_serves_file(tmp_path):
    (tmp_path / "GET_v1_search__limit=2&query=milk.json").write_text(
        json.dumps({"status": 200, "json": {"items": [1, 2]}})
    )
    resp = _client(tmp_path).get("/v1/search", params={"query": "milk", "limit": 2})
    assert resp.status_code == 200
    assert resp.json() == {"items": [1, 2]}


def test_serves_error_status(tmp_path):
    (tmp_path / "GET_v1_x.json").write_text(json.dumps({"status": 503, "json": {}}))
    assert _client(tmp_path).get("/v1/x").status_code == 503


def test_missing_recording_is_404_by_default(tmp_path):
    resp = _client(tmp_path).get("/v1/search", params={"query": "milk"})
    assert resp.status_code == 404 and isinstance(resp.json(), dict)


def test_unmatched_request_names_expected_file(tmp_path):
    with pytest.raises(AssertionError, match=r"GET_v1_search__query=milk\.json"):
        _client(tmp_path, strict=True).get("/v1/search", params={"query": "milk"})


def test_secret_params_not_in_filename(tmp_path):
    (tmp_path / "GET_v1_search__query=milk.json").write_text(
        json.dumps({"status": 200, "json": {}})
    )
    resp = _client(tmp_path).get(
        "/v1/search",
        params={"query": "milk", "publisherId": "SECRET1", "apiKey": "SECRET2"},
    )
    assert resp.status_code == 200


def test_unmatched_error_does_not_leak_secrets(tmp_path):
    with pytest.raises(AssertionError) as exc:
        _client(tmp_path, strict=True).get("/v1/search", params={"apiKey": "SECRET2"})
    assert "SECRET2" not in str(exc.value)


def test_recording_name_rules():
    def name(url, method="GET"):
        return recording_name(httpx.Request(method, url))

    assert name("https://h.test/a/b") == "GET_a_b.json"
    assert name("https://h.test/a/b?z=1&a=2") == "GET_a_b__a=2&z=1.json"
    assert (
        name("https://h.test/a?q=two words&p=50%")
        == "GET_a__p=50%25&q=two%20words.json"
    )
    assert name("https://h.test/a?q=a/b&r=x%26y") == "GET_a__q=a%2Fb&r=x%26y.json"
    assert name("https://h.test/a", "POST") == "POST_a.json"


def test_redaction_is_case_insensitive_and_covers_tokens():
    def name(url):
        return recording_name(httpx.Request("GET", url))

    assert (
        name("https://h.test/a?q=1&PublisherID=s&APIKEY=s&Api_Key=s")
        == "GET_a__q=1.json"
    )
    assert (
        name("https://h.test/a?q=1&access_token=s&token=s&Secret=s&KEY=s")
        == "GET_a__q=1.json"
    )


def test_long_recording_names_are_shortened_deterministically():
    url = "https://h.test/a?q=" + "x" * 300
    first = recording_name(httpx.Request("GET", url))
    assert first == recording_name(httpx.Request("GET", url))
    assert len(first) <= 255 and first.endswith(".json") and "~" in first
    assert first != recording_name(httpx.Request("GET", url + "y"))
