import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from connectors.base import ConnectorError
from connectors.open_prices import (
    USER_AGENT,
    Location,
    OpenPricesClient,
    chain_key,
    parse_item,
    skip_reason,
    to_raw_offer,
)
from connectors.recording import recorded_transport

RECORDINGS = Path(__file__).parent / "recordings" / "open_prices"
TODAY = date(2026, 11, 8)


def _item(**over):
    item = {
        "id": 123,
        "type": "PRODUCT",
        "product_code": "0811620020169",
        "product": {
            "product_name": "Whole Milk",
            "brands": "Horizon",
            "quantity": "1 gal",
            "product_quantity": None,
            "product_quantity_unit": None,
            "labels_tags": ["en:organic"],
        },
        "price": 4.99,
        "currency": "USD",
        "price_is_discounted": False,
        "date": "2026-11-01",
        "location": {
            "type": "OSM",
            "osm_type": "NODE",
            "osm_id": 1392117416,
            "osm_name": "Safeway",
            "osm_brand": "Safeway",
            "osm_address_city": "Mountain View",
            "osm_address_postcode": "94043",
            "osm_address_country_code": "US",
        },
        "proof": {"type": "PRICE_TAG"},
        "source": "Smoothie - OpenFoodFacts",
        "duplicate_of": None,
    }
    item.update(over)
    return item


def _parse(**over):
    # Mirror the client: floats decoded as Decimal.
    return parse_item(json.loads(json.dumps(_item(**over)), parse_float=Decimal))


def _reason(obs, include_assisted=True):
    return skip_reason(
        obs, country="US", currency="USD", include_assisted=include_assisted, today=TODAY
    )


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def _client(transport, ft):
    http = httpx.Client(transport=transport, headers={"User-Agent": USER_AGENT})
    return OpenPricesClient(http, sleep=ft.sleep, clock=ft.clock)


def test_fetch_window_pages_and_params():
    seen: list[httpx.Request] = []
    inner = recorded_transport(RECORDINGS, strict=True)

    def handler(request):
        seen.append(request)
        return inner.handle_request(request)

    ft = FakeTime()
    client = _client(httpx.MockTransport(handler), ft)
    obs = client.fetch_window(date(2026, 8, 10), "USD")
    ids = [o.price_id for o in obs]
    assert len(ids) == len(set(ids)) == 6
    assert ids.count(361106) == 1
    assert len(seen) == 2
    for request in seen:
        assert request.url.path == "/api/v1/prices"  # never /price-tags or /proofs
        assert request.headers["User-Agent"] == USER_AGENT
    params = seen[0].url.params
    assert params["currency"] == "USD"
    assert params["date__gte"] == "2026-08-10"
    assert params["type"] == "PRODUCT"
    assert params["location__type"] == "OSM"
    assert params["duplicate_of__isnull"] == "true"
    assert params["order_by"] == "-id"
    assert params["size"] == "100"
    assert [r.url.params["page"] for r in seen] == ["1", "2"]
    assert ft.sleeps and all(s >= 1.0 for s in ft.sleeps)
    assert client.unparsed == 0


def test_price_parsed_exactly():
    obs = _parse(price=4.99)
    assert obs.price == Decimal("4.99")
    assert obs.price != Decimal(4.99)


def test_three_decimal_price_skipped():
    assert _reason(_parse(price=1.255)) == "precision"
    kept = _parse(price=1.250)
    assert _reason(kept) is None
    assert kept.price == Decimal("1.25")


TOMORROW = (TODAY + timedelta(days=1)).isoformat()
NULL_LOC = {
    "type": "OSM",
    "osm_type": None,
    "osm_id": None,
    "osm_name": None,
    "osm_brand": None,
}


def _loc(**over):
    return {**_item()["location"], **over}


@pytest.mark.parametrize(
    "over, include_assisted, expected",
    [
        ({"type": "CATEGORY"}, True, "type"),
        ({"duplicate_of": 5}, True, "duplicate"),
        ({"currency": "EUR"}, True, "currency"),
        ({"location": _loc(osm_address_country_code="EC")}, True, "country"),
        ({"location": NULL_LOC}, True, "no_location"),
        ({"location": _loc(type="ONLINE")}, True, "online"),
        ({"location": _loc(osm_name="!!! ***", osm_brand=None)}, True, "no_location"),
        (
            {"source": "Open Prices Web App - /experiments/proof-price-tag-assistant"},
            True,
            None,
        ),
        (
            {"source": "Open Prices Web App - /experiments/proof-price-tag-assistant"},
            False,
            "assisted",
        ),
        ({"product_code": "abc"}, True, "no_code"),
        ({"product": {"product_name": None}}, True, "no_name"),
        ({"date": TOMORROW}, True, "future_date"),
        ({"price": 0}, True, "price"),
        ({}, True, None),
    ],
)
def test_skip_reasons(over, include_assisted, expected):
    obs = _parse(**over)
    assert obs is not None
    assert _reason(obs, include_assisted) == expected


@pytest.mark.parametrize("over", [{"id": None}, {"price": None}, {"date": None}, {"date": "nope"}, {"price": "x"}])
def test_parse_item_malformed(over):
    assert _parse(**over) is None


def test_quantity_fallback():
    product = {"product_name": "Juice", "quantity": None, "product_quantity": 1500, "product_quantity_unit": "ml"}
    assert _parse(product=product).quantity == "1500 ml"
    product = {"product_name": "Juice", "quantity": None, "product_quantity": None, "product_quantity_unit": None}
    assert _parse(product=product).quantity is None


def test_to_raw_offer():
    obs = _parse()
    raw = to_raw_offer(obs, datetime(2026, 11, 8, tzinfo=UTC))
    assert raw.retailer == "op-safeway"
    assert raw.retailer_item_id == "0811620020169@osm-n1392117416"
    assert raw.upc == "0811620020169"
    assert raw.url == "https://prices.openfoodfacts.org/prices/123"
    assert raw.title == "Whole Milk"
    assert raw.brand == "Horizon"
    assert raw.size_text == "1 gal"
    assert raw.price == Decimal("4.99")
    assert raw.currency == "USD"
    assert obs.labels == frozenset({"en:organic"})


def _location(name, brand):
    return Location("NODE", 1, name, brand, None, None, "US", "OSM")


@pytest.mark.parametrize(
    "name, brand, expected",
    [
        ("Safeway", "Safeway", ("op-safeway", "Safeway")),
        ("Draeger's", None, ("op-draegers", "Draeger's")),
        ("Smart & Final extra!", "Smart & Final", ("op-smart-and-final", "Smart & Final")),
        ("Smart & Final extra!", None, ("op-smart-and-final-extra", "Smart & Final extra!")),
        (" Whole Foods  Market ", "  ", ("op-whole-foods-market", "Whole Foods Market")),
    ],
)
def test_chain_key(name, brand, expected):
    assert chain_key(_location(name, brand)) == expected


def test_location_code():
    assert _location("x", None).code == "osm-n1"


def test_server_error_retries_then_raises():
    ft = FakeTime()
    client = _client(recorded_transport(RECORDINGS, strict=True), ft)
    with pytest.raises(ConnectorError) as exc:
        client.fetch_window(date(2026, 1, 1), "USD")
    assert "?" not in str(exc.value)
    assert "HTTP 503" in str(exc.value)
    assert [s for s in ft.sleeps if s >= 2] == [2, 4]


def test_page_cap():
    def handler(request):
        return httpx.Response(200, json={"items": [], "page": 1, "pages": 501})

    ft = FakeTime()
    client = _client(httpx.MockTransport(handler), ft)
    with pytest.raises(ConnectorError, match="exceeds 500 pages"):
        client.fetch_window(date(2026, 8, 10), "USD")


def test_unparseable_items_counted():
    def handler(request):
        return httpx.Response(
            200, json={"items": [{"id": 1}, "junk", _item(id=9)], "page": 1, "pages": 1}
        )

    client = _client(httpx.MockTransport(handler), FakeTime())
    assert [o.price_id for o in client.fetch_window(date(2026, 8, 10), "USD")] == [9]
    assert client.unparsed == 2


def _with_location(**fields):
    item = _item()
    item["location"] = {**item["location"], **fields}
    return parse_item(json.loads(json.dumps(item), parse_float=Decimal))


def test_location_coordinates_parsed():
    loc = _with_location(osm_lat=37.4031206, osm_lon=-122.079306).location
    assert (loc.lat, loc.lon) == (37.4031206, -122.079306)
    assert isinstance(loc.lat, float) and isinstance(loc.lon, float)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"osm_lat": None, "osm_lon": None},
        {"osm_lat": "37.4", "osm_lon": "-122.0"},
        {"osm_lat": True, "osm_lon": -122.0},
        {"osm_lat": 91, "osm_lon": -122.0},
        {"osm_lat": 37.4, "osm_lon": 181},
        {"osm_lat": 37.4},
    ],
)
def test_location_coordinates_missing_or_bad_are_none(fields):
    loc = _with_location(**fields).location
    assert (loc.lat, loc.lon) == (None, None)


def test_recorded_locations_carry_coordinates():
    rec = Path(__file__).parent / "recordings" / "open_prices"
    items = []
    for path in sorted(rec.glob("*.json")):
        body = json.loads(path.read_text(), parse_float=Decimal)["json"]
        items.extend(body.get("items", []))
    assert items
    for item in items:
        loc = parse_item(item).location
        assert loc.lat is not None and loc.lon is not None


def test_parse_item_reads_categories_and_raw_offer_path():
    product = {"product_name": "Oat Drink", "categories_tags": ["en:plant-based-milks", 5, "fr:x"]}
    obs = _parse(product=product)
    assert obs.categories == ("en:plant-based-milks", "fr:x")
    assert _parse(product={"product_name": "x"}).categories == ()
    assert _parse(product={"categories_tags": "en:milks"}).categories == ()


def test_budget_wait_replaces_own_pacing():
    inner = recorded_transport(RECORDINGS, strict=True)
    ft = FakeTime()
    waits: list[str] = []
    http = httpx.Client(transport=inner, headers={"User-Agent": USER_AGENT})
    client = OpenPricesClient(http, sleep=ft.sleep, clock=ft.clock, budget_wait=waits.append)
    client.fetch_window(date(2026, 8, 10), "USD")
    assert waits == ["openprices", "openprices"]  # one token per request
    assert ft.sleeps == []  # the shared bucket paces; no local 1 req/s sleep
