import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from connectors import registry
from connectors.base import ConnectorError
from connectors.live import BudgetExhausted
from connectors.off_search import FIELDS, OffPricesSource
from connectors.open_prices import OpenPricesClient
from connectors.recording import recorded_transport
from enrichment.off import USER_AGENT

RECORDINGS = Path(__file__).parent / "recordings"
TODAY = date(2026, 11, 8)


class FakeBudget:
    def __init__(self, exhausted_on=None):
        self.calls = []
        self.exhausted_on = exhausted_on

    def acquire(self, key):
        if key == self.exhausted_on:
            raise BudgetExhausted(3)
        self.calls.append(("acquire", key))

    def report(self, key, status, retry_after=None):
        self.calls.append(("report", key, status))

    def keys(self, kind="acquire"):
        return [c[1] for c in self.calls if c[0] == kind]


def source(transport):
    client = httpx.Client(transport=transport, headers={"User-Agent": USER_AGENT})
    return OffPricesSource(client=client, today=lambda: TODAY)


def recorded():
    class Both:
        def __init__(self):
            self.off = recorded_transport(RECORDINGS / "off_search", strict=True)
            self.prices = recorded_transport(RECORDINGS / "open_prices", strict=True)
            self.requests = []

        def __call__(self, request):
            self.requests.append(request)
            side = self.off if request.url.host.startswith("world.") else self.prices
            return side.handle_request(request)

    return Both()


def test_search_then_prices():
    both = recorded()
    budget = FakeBudget()
    result = source(httpx.MockTransport(both)).live_search("oat milk", 25, budget)
    # The prices token is taken before the search and spent on the prices request.
    assert budget.keys() == ["openprices", "off-search"]
    assert len(result.off_records) == 5 and result.skipped == 0
    by_upc = {r.upc: r for r in result.off_records}
    assert "en:vegan" in by_upc["0044100156182"].labels
    assert by_upc["0044100156182"].categories and by_upc["0044100156182"].quantity_unit == "ml"
    assert len(result.observations) == 6
    assert {o.product_code for o in result.observations} <= set(by_upc)
    assert result.offers == []
    # the prices request carries the codes, a 90-day window and the site currency
    prices_req = both.requests[-1]
    assert prices_req.url.params["product_code__in"].split(",") == list(by_upc)
    assert prices_req.url.params["date__gte"] == (TODAY - timedelta(days=90)).isoformat()
    assert prices_req.url.params["currency"] == "USD"


def test_us_only_and_fields_params():
    both = recorded()
    source(httpx.MockTransport(both)).live_search("oat milk", 25, FakeBudget())
    params = both.requests[0].url.params
    assert params["tagtype_0"] == "countries"
    assert params["tag_contains_0"] == "contains"
    assert params["tag_0"] == "united-states"
    assert params["fields"] == FIELDS == (
        "code,product_name,brands,quantity,product_quantity,product_quantity_unit,"
        "labels_tags,categories_tags"
    )
    assert params["search_terms"] == "oat milk" and params["json"] == "1"
    assert params["page_size"] == "50"
    assert both.requests[0].headers["User-Agent"] == USER_AGENT


def _sequence(statuses):
    ok = json.loads((RECORDINGS / "off_search" / "503.json").read_text())
    ok_body = next(RECORDINGS.glob("off_search/*oat%20milk*")).read_text()
    seen = []

    def handler(request):
        if request.url.host.startswith("world."):
            seen.append(request)
            status = statuses[len(seen) - 1]
            if status == 503:
                return httpx.Response(503, json=ok["json"])
            return httpx.Response(200, json=json.loads(ok_body)["json"])
        return recorded_transport(RECORDINGS / "open_prices", strict=True).handle_request(request)

    return handler, seen


def test_off_503_retried_twice_each_with_token():
    handler, seen = _sequence([503, 200])
    budget = FakeBudget()
    result = source(httpx.MockTransport(handler)).live_search("oat milk", 25, budget)
    assert len(result.off_records) == 5
    assert budget.keys().count("off-search") == 2
    assert [c for c in budget.calls if c[0] == "report" and c[1] == "off-search"] == [
        ("report", "off-search", 503),
        ("report", "off-search", 200),
    ]


def test_off_503_three_times_fails():
    handler, seen = _sequence([503, 503, 503])
    budget = FakeBudget()
    with pytest.raises(ConnectorError):
        source(httpx.MockTransport(handler)).live_search("oat milk", 25, budget)
    assert budget.keys() == ["openprices"] + ["off-search"] * 3
    assert [c[2] for c in budget.calls if c[0] == "report"] == [503, 503, 503]
    assert len(seen) == 3


def test_429_reports_and_fails_without_retry():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={})

    budget = FakeBudget()
    with pytest.raises(ConnectorError):
        source(httpx.MockTransport(handler)).live_search("oat milk", 25, budget)
    assert budget.calls == [
        ("acquire", "openprices"), ("acquire", "off-search"), ("report", "off-search", 429),
    ]
    assert len(seen) == 1


def test_transport_error_reports_none():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    budget = FakeBudget()
    with pytest.raises(ConnectorError):
        source(httpx.MockTransport(handler)).live_search("oat milk", 25, budget)
    assert budget.calls[-1] == ("report", "off-search", None)


def test_no_codes_no_prices_request():
    both = recorded()
    budget = FakeBudget()
    result = source(httpx.MockTransport(both)).live_search("zzzz-nothing", 25, budget)
    assert result.observations == [] and result.off_records == [] and result.skipped == 0
    assert budget.keys() == ["openprices", "off-search"]
    assert [r.url.host for r in both.requests] == ["world.openfoodfacts.org"]


def test_unparseable_codes_are_skipped_and_counted():
    body = {"products": [{"code": "abc", "product_name": "x"}, {"product_name": "y"}]}
    result = source(httpx.MockTransport(lambda r: httpx.Response(200, json=body))).live_search(
        "x", 25, FakeBudget()
    )
    assert result.skipped == 2 and result.off_records == []


def test_budget_exhausted_propagates():
    both = recorded()
    with pytest.raises(BudgetExhausted) as info:
        source(httpx.MockTransport(both)).live_search("oat milk", 25, FakeBudget("off-search"))
    assert info.value.wait == 3 and both.requests == []


def test_prices_budget_exhausted_before_any_search():
    """An Open Prices shortage must not cost an OFF search that the requeued job repeats."""
    both = recorded()
    budget = FakeBudget("openprices")
    with pytest.raises(BudgetExhausted):
        source(httpx.MockTransport(both)).live_search("oat milk", 25, budget)
    assert both.requests == []
    assert budget.keys() == []


def test_prepaid_prices_token_is_used_once():
    both = recorded()
    budget = FakeBudget()
    source(httpx.MockTransport(both)).live_search("oat milk", 25, budget)
    assert budget.keys().count("openprices") == 1
    assert [r.url.host for r in both.requests] == ["world.openfoodfacts.org", "prices.openfoodfacts.org"]


def test_deadline_stops_a_slow_job():
    now = [0.0]

    def clock():
        return now[0]

    def handler(request):
        now[0] += 200  # every response takes longer than the whole job allowance
        return httpx.Response(503, json={})

    src = OffPricesSource(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        today=lambda: TODAY,
        clock=clock,
    )
    budget = FakeBudget()
    with pytest.raises(ConnectorError, match="deadline"):
        src.live_search("oat milk", 25, budget)
    assert budget.keys() == ["openprices", "off-search"]  # no second request after the deadline


def test_fetch_codes_non_200_reports_and_raises():
    budget = FakeBudget()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503, json={})))
    with pytest.raises(ConnectorError):
        OpenPricesClient(client=client).fetch_codes(["0044100156182"], TODAY, "USD", budget)
    assert budget.calls == [("acquire", "openprices"), ("report", "openprices", 503)]


def test_fetch_codes_params_and_cap():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    codes = [str(i).zfill(13) for i in range(60)]
    OpenPricesClient(client=httpx.Client(transport=httpx.MockTransport(handler))).fetch_codes(
        codes, TODAY, "USD", FakeBudget()
    )
    p = seen[0].url.params
    assert len(p["product_code__in"].split(",")) == 50
    assert (p["type"], p["location__type"], p["duplicate_of__isnull"], p["order_by"]) == (
        "PRODUCT",
        "OSM",
        "true",
        "-id",
    )
    assert (p["size"], p["page"]) == ("100", "1")


def test_live_off_off_by_default():
    assert "off-prices" not in registry.live_sources({})
    assert "off-prices" not in registry.live_source_info({})
    sources = registry.live_sources({}, live_off=True)
    try:
        assert isinstance(sources["off-prices"], OffPricesSource)
    finally:
        for s in sources.values():
            s.close()
    info = registry.live_source_info({}, live_off=True)["off-prices"]
    assert info.budget_keys == ("off-search", "openprices")


@pytest.mark.parametrize(
    ("header", "expected"),
    [("30", 30.0), (" 2.5 ", 2.5), ("-1", None), ("nan", None), ("inf", None), ("Wed, 21 Oct 2015", None), (None, None)],
)
def test_retry_after_is_a_public_live_helper(header, expected):
    import connectors.off_search
    import connectors.open_prices
    import connectors.walmart
    from connectors.live import retry_after

    headers = {"Retry-After": header} if header is not None else {}
    assert retry_after(httpx.Response(429, headers=headers)) == expected
    for module in (connectors.off_search, connectors.open_prices, connectors.walmart):
        assert not hasattr(module, "_retry_after")
