import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from connectors import registry
from connectors.base import ConnectorError
from connectors.cj import CjConnector, slug

RECORDINGS = Path(__file__).parent / "recordings" / "cj"
TOKEN = "tok-SECRET"
CID = "9999999"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _body(name):
    return json.loads((RECORDINGS / f"{name}.json").read_text())["body"]


def _product():
    """A real product (Thrive Market, in stock, new) flipped to joined."""
    item = copy.deepcopy(_body("gtin_860010924006_notjoined")["data"]["shoppingProducts"]["resultList"][0])
    item["joinedStatus"] = True
    return item


def _wrap(*items):
    return {"data": {"shoppingProducts": {"totalCount": len(items), "count": len(items), "resultList": list(items)}}}


def _connector(responder):
    seen = []

    def handler(request):
        seen.append(request)
        out = responder(request)
        if isinstance(out, httpx.Response):
            return out
        return httpx.Response(200, json=out)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return CjConnector(TOKEN, CID, client=client, clock=lambda: NOW), seen


def _one(item):
    c, _ = _connector(lambda r: _wrap(item))
    return c.search("x")


class SpyBudget:
    def __init__(self):
        self.calls = []

    def acquire(self, key):
        self.calls.append(("acquire", key))

    def report(self, key, status, retry_after=None):
        self.calls.append(("report", key, status, retry_after))


def test_request_uses_variables_and_header_only_token():
    c, seen = _connector(lambda r: _wrap())
    c.search("paper towels", limit=7)
    req = seen[0]
    assert str(req.url) == "https://ads.api.cj.com/query" and req.method == "POST"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(req.content)
    assert body["variables"] == {"companyId": CID, "limit": 7, "keywords": ["paper towels"]}
    assert "JOINED" in body["query"] and "NOT_JOINED" not in body["query"]
    assert "paper towels" not in body["query"] and CID not in body["query"]
    assert TOKEN not in req.content.decode()


@pytest.mark.parametrize(
    "name",
    [
        "gtin_850017346062_notjoined",
        "gtin_850029345411_notjoined",
        "gtin_860010924006_notjoined",
        "keyword_paper_towels_notjoined",
        "keyword_protein_powder_iherb_notjoined",
    ],
)
def test_not_joined_fixtures_map_to_no_offers(name):
    body = _body(name)
    c, _ = _connector(lambda r: body)
    result = c.search("x")
    assert result.offers == []
    assert result.skipped == len(body["data"]["shoppingProducts"]["resultList"])


def test_empty_fixtures():
    for name in ("gtin_none", "joined_paper_towels"):
        body = _body(name)
        c, _ = _connector(lambda r: body)
        result = c.search("x")
        assert result.offers == [] and result.skipped == 0


def test_joined_product_maps_fields():
    result = _one(_product())
    assert result.skipped == 0
    (o,) = result.offers
    assert o.retailer == "cj-thrive-market"
    assert o.retailer_item_id == "860010924006"
    assert o.title == "Algae Cooking Club Algae Cooking Oil 16 fl oz bottle"
    assert o.price == Decimal("17.99")  # real sale price below price
    assert o.currency == "USD"
    assert o.url == "https://thrivemarket.com/p/algae-cooking-club-cooking-oil"
    assert o.upc == "860010924006"
    assert o.brand == "Algae Cooking Club"
    assert o.size_text == "1"  # unitPricingMeasure wins over size, per spec
    assert o.fields == {
        "category_path": "Pantry > Ghee > Oils & Vinegars > Oils > Specialty Oil",
        "retailer_name": "Thrive Market",
    }
    assert o.fetched_at == NOW


def test_size_falls_back_and_category_prefers_google():
    item = _product()
    item["unitPricingMeasure"] = None
    item["googleProductCategory"] = {"name": "Food > Oils"}
    (o,) = _one(item).offers
    assert o.size_text == "fl oz"
    assert o.fields["category_path"] == "Food > Oils"
    item["size"] = None
    item["productType"] = []
    item["googleProductCategory"] = None
    (o,) = _one(item).offers
    assert o.size_text is None and "category_path" not in o.fields


def test_non_numeric_gtin_gives_no_upc():
    item = _product()
    item["gtin"] = "ABC123"
    (o,) = _one(item).offers
    assert o.upc is None
    item["gtin"] = ""
    (o,) = _one(item).offers
    assert o.upc is None


def _sale(item, amount="10.00", currency="USD", start=None, end=None):
    item["salePrice"] = {"amount": amount, "currency": currency}
    item["salePriceEffectiveDateStart"] = start
    item["salePriceEffectiveDateEnd"] = end


@pytest.mark.parametrize(
    "kw, expected",
    [
        ({}, "10.00"),
        ({"start": "2026-11-01T00:00:00Z", "end": "2026-11-30T00:00:00Z"}, "10.00"),
        ({"start": "2026-11-11T00:00:00Z"}, "19.99"),  # not started
        ({"end": "2026-11-09T00:00:00Z"}, "19.99"),  # expired
        ({"end": "garbage"}, "19.99"),  # unreadable date: sale not trusted
        ({"amount": "19.99"}, "19.99"),  # not less than price
        ({"amount": "25.00"}, "19.99"),  # greater than price
        ({"amount": "0"}, "19.99"),
        ({"amount": "-1"}, "19.99"),
        ({"amount": "abc"}, "19.99"),
        ({"currency": "EUR"}, "19.99"),  # never mix currencies
    ],
)
def test_sale_price_rules(kw, expected):
    item = _product()
    _sale(item, **kw)
    (o,) = _one(item).offers
    assert o.price == Decimal(expected)


def test_missing_sale_uses_price():
    item = _product()
    item["salePrice"] = None
    (o,) = _one(item).offers
    assert o.price == Decimal("19.99")


def _mutate(**changes):
    item = _product()
    item.update(changes)
    return item


@pytest.mark.parametrize(
    "item",
    [
        _mutate(joinedStatus=False),
        _mutate(joinedStatus=None),
        _mutate(condition="Used"),
        _mutate(condition="Refurbished"),
        _mutate(availability="out of stock"),
        _mutate(availability=None),
        _mutate(advertiserCountry="CA"),
        _mutate(targetCountry="GB"),
        _mutate(title=""),
        _mutate(title=None),
        _mutate(link="ftp://x.example/p"),
        _mutate(link=""),
        _mutate(link="javascript:alert(1)"),
        _mutate(price=None),
        _mutate(price={"amount": "0", "currency": "USD"}),
        _mutate(price={"amount": "-3", "currency": "USD"}),
        _mutate(price={"amount": "NaN", "currency": "USD"}),
        _mutate(price={"amount": "Infinity", "currency": "USD"}),
        _mutate(price={"amount": "9.99", "currency": "EUR"}),
        _mutate(price={"amount": "9.99", "currency": None}),
        _mutate(advertiserName=""),
        _mutate(id=None),
        "not a dict",
    ],
)
def test_skip_rules(item):
    result = _one(item)
    assert result.offers == [] and result.skipped == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"condition": "NEW"},
        {"condition": None},
        {"condition": ""},
        {"availability": "in_stock"},
        {"availability": "IN STOCK"},
        {"advertiserCountry": None, "targetCountry": None},
        {"advertiserCountry": "us", "targetCountry": "US"},
        {"link": "http://thrivemarket.com/p/x"},
    ],
)
def test_accept_variants(overrides):
    result = _one(_mutate(**overrides))
    assert len(result.offers) == 1 and result.skipped == 0


def test_mixed_results_count_skips():
    c, _ = _connector(lambda r: _wrap(_product(), _mutate(condition="Used"), _mutate(joinedStatus=False)))
    result = c.search("x")
    assert len(result.offers) == 1 and result.skipped == 2


def test_slug():
    assert slug("BJ's Wholesale Club") == "bj-s-wholesale-club"
    assert slug("  Vitacost.com ") == "vitacost-com"
    assert slug("Thrive Market") == "thrive-market"
    item = _mutate(advertiserName="A" * 80)
    (o,) = _one(item).offers
    assert o.retailer == "cj-" + "a" * 47 and len(o.retailer) == 50
    item = _mutate(advertiserName="!!!")
    assert _one(item).offers == []


def _gtin_responder(hits):
    asked = []

    def respond(request):
        gtin = json.loads(request.content)["variables"]["gtin"]
        asked.append(gtin)
        return _wrap(_product()) if gtin in hits else _wrap()

    return respond, asked


def test_gtin_variant_order_with_leading_zeros():
    respond, asked = _gtin_responder(hits=set())
    c, _ = _connector(respond)
    c.lookup_gtin("000012345678")
    assert asked == ["000012345678", "12345678", "0000012345678", "00000012345678"]


def test_gtin_variants_deduplicated_in_order():
    respond, asked = _gtin_responder(hits=set())
    c, _ = _connector(respond)
    budget = SpyBudget()
    c.lookup_gtin("12345678", budget)
    assert asked == ["12345678", "000012345678", "0000012345678", "00000012345678"]
    assert budget.calls.count(("acquire", "cj")) == 4
    assert budget.calls.count(("report", "cj", 200, None)) == 4


def test_gtin_strips_then_pads():
    respond, asked = _gtin_responder(hits=set())
    c, _ = _connector(respond)
    c.lookup_gtin("00860010924006", None)
    assert asked == ["00860010924006", "860010924006", "0860010924006"]


def test_gtin_stops_at_first_hit_and_charges_per_try():
    respond, asked = _gtin_responder(hits={"0860010924006"})
    c, _ = _connector(respond)
    budget = SpyBudget()
    result = c.lookup_gtin("860010924006", budget)
    assert len(result.offers) == 1
    assert asked == ["860010924006", "0860010924006"]
    assert [x for x in budget.calls if x[0] == "acquire"] == [("acquire", "cj")] * 2


def test_gtin_first_variant_hit_makes_one_request():
    respond, asked = _gtin_responder(hits={"860010924006"})
    c, _ = _connector(respond)
    c.lookup_gtin("860010924006")
    assert asked == ["860010924006"]


def test_gtin_error_reports_and_raises():
    def boom(request):
        raise httpx.ConnectError(f"dns failure for {TOKEN}")

    c, _ = _connector(boom)
    budget = SpyBudget()
    with pytest.raises(ConnectorError) as exc:
        c.lookup_gtin("860010924006", budget)
    assert budget.calls == [("acquire", "cj"), ("report", "cj", None, None)]
    assert TOKEN not in str(exc.value)


def test_live_search_budget_acquire_and_report():
    c, seen = _connector(lambda r: _wrap(_product()))
    budget = SpyBudget()
    result = c.live_search("oil", 10, budget)
    assert len(result.offers) == 1 and result.skipped == 0
    assert budget.calls == [("acquire", "cj"), ("report", "cj", 200, None)]
    assert len(seen) == 1


def test_live_search_reports_retry_after_and_raises():
    c, _ = _connector(lambda r: httpx.Response(429, headers={"Retry-After": "30"}, text="slow"))
    budget = SpyBudget()
    with pytest.raises(ConnectorError, match="429"):
        c.live_search("oil", 10, budget)
    assert budget.calls == [("acquire", "cj"), ("report", "cj", 429, 30.0)]


def test_live_search_transport_error_reports_none():
    def boom(request):
        raise httpx.ReadTimeout("slow")

    c, _ = _connector(boom)
    budget = SpyBudget()
    with pytest.raises(ConnectorError, match=r"CJ request failed \(ReadTimeout\)"):
        c.live_search("oil", 10, budget)
    assert budget.calls == [("acquire", "cj"), ("report", "cj", None, None)]


def _assert_clean(exc):
    text = f"{exc.value} {exc.value!r}"
    assert TOKEN not in text and CID not in text
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


def test_http_error_has_no_secret():
    c, _ = _connector(lambda r: httpx.Response(400, text=f"bad companyId {CID} token {TOKEN}"))
    with pytest.raises(ConnectorError, match="CJ search failed with HTTP 400") as exc:
        c.search("x")
    _assert_clean(exc)


def test_graphql_errors_in_200_have_no_text():
    c, _ = _connector(lambda r: {"errors": [{"message": f"companyId {CID} not allowed"}], "data": None})
    with pytest.raises(ConnectorError) as exc:
        c.search("x")
    assert str(exc.value) == "CJ search returned GraphQL errors"
    _assert_clean(exc)


def test_unreadable_and_unexpected_shapes():
    for resp in (
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, json={"data": None}),
        httpx.Response(200, json={"data": {"shoppingProducts": {"resultList": "x"}}}),
    ):
        c, _ = _connector(lambda r, resp=resp: resp)
        with pytest.raises(ConnectorError):
            c.search("x")


def test_transport_error_has_no_secret():
    def boom(request):
        raise httpx.ConnectError(f"connect to host with {TOKEN} {CID}")

    c, _ = _connector(boom)
    with pytest.raises(ConnectorError) as exc:
        c.search("x")
    assert str(exc.value) == "CJ request failed (ConnectError)"
    _assert_clean(exc)


def test_repr_hides_everything():
    c, _ = _connector(lambda r: _wrap())
    assert repr(c) == "CjConnector()"
    assert TOKEN not in repr(c) and CID not in repr(c) and TOKEN not in str(c)


def test_class_attributes():
    from datetime import timedelta

    assert CjConnector.name == "cj"
    assert CjConnector.budget_keys == ("cj",)
    assert CjConnector.max_cache_age == timedelta(hours=24)
    assert CjConnector.refresh_after == timedelta(hours=12)
    assert CjConnector.records_price_history is False


def test_close_closes_client():
    c, _ = _connector(lambda r: _wrap())
    c.close()
    assert c._client.is_closed


def test_registry_spec_and_build_from_env():
    spec = registry.SPECS["cj"]
    assert spec.path == "connectors.cj:CjConnector"
    assert spec.required == {"token": "CJ_PERSONAL_ACCESS_TOKEN", "company_id": "CJ_PUBLISHER_ID"}
    env = {"CJ_PERSONAL_ACCESS_TOKEN": TOKEN, "CJ_PUBLISHER_ID": CID}
    assert isinstance(registry.get("cj", env), CjConnector)
    assert "cj" in registry.available(env)
    assert "cj" not in registry.available({"CJ_PERSONAL_ACCESS_TOKEN": TOKEN})
    assert "cj" in registry.live_source_info(env)
    with pytest.raises(ConnectorError, match="CJ_PUBLISHER_ID"):
        registry.get("cj", {"CJ_PERSONAL_ACCESS_TOKEN": TOKEN})


def test_budget_configured(settings):
    b = settings.SOURCE_BUDGETS["cj"]
    assert b["capacity"] == 2 and b["refill_per_second"] == 0.5 and b["daily_limit"] == 3000
