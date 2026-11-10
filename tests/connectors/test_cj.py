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
    assert o.size_text is None  # "1" and "fl oz" are each incomplete; never joined
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
    assert o.size_text is None  # unit-only "fl oz" has no number
    assert o.fields["category_path"] == "Food > Oils"
    item["size"] = None
    item["productType"] = []
    item["googleProductCategory"] = None
    (o,) = _one(item).offers
    assert o.size_text is None and "category_path" not in o.fields


@pytest.mark.parametrize(
    "measure, size, expected",
    [
        ("1", "fl oz", None),
        (None, "1LB", "1LB"),
        ("16 oz", "2LB", "16 oz"),
        ("1", "2LB", "2LB"),
        (" 16 oz ", None, "16 oz"),
        ("oz", "fl oz", None),
        (None, None, None),
    ],
)
def test_size_text_needs_digit_and_letter(measure, size, expected):
    (o,) = _one(_mutate(unitPricingMeasure=measure, size=size)).offers
    assert o.size_text == expected


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
    assert asked == ["12345678", "0000012345678", "00000012345678"]
    assert budget.calls.count(("acquire", "cj")) == 3
    assert budget.calls.count(("report", "cj", 200, None)) == 3


def test_gtin_13_digit_leading_zero_tries_upc_a_first():
    respond, asked = _gtin_responder(hits=set())
    c, _ = _connector(respond)
    c.lookup_gtin("0860010924006")
    assert asked == ["860010924006", "0860010924006", "00860010924006"]


def test_gtin_13_digit_hit_on_upc_a_costs_one_request():
    respond, asked = _gtin_responder(hits={"860010924006"})
    c, _ = _connector(respond)
    budget = SpyBudget()
    assert len(c.lookup_gtin("0860010924006", budget).offers) == 1
    assert asked == ["860010924006"]
    assert budget.calls.count(("acquire", "cj")) == 1


def test_gtin_13_digit_without_leading_zero_starts_with_input():
    respond, asked = _gtin_responder(hits=set())
    c, _ = _connector(respond)
    c.lookup_gtin("5012345678900")
    assert asked == ["5012345678900", "05012345678900"]


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


# has_joined_advertisers (#111 F2b)


def test_has_joined_advertisers_one_budgeted_request():
    c, seen = _connector(lambda r: _wrap(_product()))
    budget = SpyBudget()
    assert c.has_joined_advertisers(budget) is True
    assert budget.calls == [("acquire", "cj"), ("report", "cj", 200, None)]
    (request,) = seen
    sent = json.loads(request.content)
    assert "partnerStatus: JOINED" in sent["query"] and 'advertiserCountries: ["US"]' in sent["query"]
    assert sent["variables"]["limit"] == 1
    assert sent["variables"].get("gtin") is None and sent["variables"].get("keywords") is None


def test_has_joined_advertisers_false_on_zero_total():
    c, _ = _connector(lambda r: _body("joined_paper_towels"))
    assert c.has_joined_advertisers() is False


def test_has_joined_advertisers_error_reports_and_raises():
    c, _ = _connector(lambda r: httpx.Response(503))
    budget = SpyBudget()
    with pytest.raises(ConnectorError):
        c.has_joined_advertisers(budget)
    assert budget.calls == [("acquire", "cj"), ("report", "cj", 503, None)]


# error classes (#111 F5)


@pytest.mark.parametrize(
    "resp, retryable",
    [
        (httpx.Response(400, text=f"bad {TOKEN}"), False),
        (httpx.Response(200, json={"errors": [{"message": CID}], "data": None}), False),
        (httpx.Response(429), True),
        (httpx.Response(500), True),
        (httpx.Response(503), True),
    ],
)
def test_request_error_retryable_flag(resp, retryable):
    from connectors.cj import CjRequestError

    c, _ = _connector(lambda r: resp)
    with pytest.raises(CjRequestError) as exc:
        c.lookup_gtin("860010924006")
    assert exc.value.retryable is retryable
    _assert_clean(exc)


def test_transport_error_is_retryable():
    def boom(request):
        raise httpx.ConnectError(f"{TOKEN}")

    c, _ = _connector(boom)
    with pytest.raises(ConnectorError) as exc:
        c.lookup_gtin("860010924006")
    assert getattr(exc.value, "retryable", True) is True


# 14-digit GTIN (#111 F6)


def test_14_digit_gtin_with_leading_zero_maps_to_13():
    item = _product()
    item["gtin"] = "00860010924006"
    (o,) = _one(item).offers
    assert o.upc == "0860010924006"


def test_14_digit_gtin_links_to_padded_12_digit_identity():
    from catalog.identity import product_identity

    a, b = _product(), _product()
    a["gtin"], b["gtin"] = "00860010924006", "860010924006"
    (oa,), (ob,) = _one(a).offers, _one(b).offers
    assert product_identity(oa) == product_identity(ob) == "upc:0860010924006"


def test_14_digit_gtin_without_leading_zero_kept():
    item = _product()
    item["gtin"] = "10860010924003"
    (o,) = _one(item).offers
    assert o.upc == "10860010924003"


# date-only sale end (#111 F7)


@pytest.mark.parametrize(
    "now, end, expected",
    [
        (datetime(2026, 11, 30, 23, 59, 59, 999000, tzinfo=UTC), "2026-11-30", "10.00"),
        (datetime(2026, 11, 30, 12, 0, tzinfo=UTC), "2026-11-30", "10.00"),
        (datetime(2026, 12, 1, 0, 0, tzinfo=UTC), "2026-11-30", "19.99"),
        (datetime(2026, 11, 30, 12, 0, tzinfo=UTC), "2026-11-30T00:00:00Z", "19.99"),  # datetime as given
        (datetime(2026, 11, 29, 23, 0, tzinfo=UTC), "2026-11-30T00:00:00Z", "10.00"),
    ],
)
def test_date_only_sale_end_is_inclusive(now, end, expected):
    item = _product()
    _sale(item, start="2026-11-01", end=end)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_wrap(item))))
    c = CjConnector(TOKEN, CID, client=client, clock=lambda: now)
    (o,) = c.search("x").offers
    assert o.price == Decimal(expected)


def _with_shipping(shipping):
    item = _product()
    item["shipping"] = shipping
    return _one(item).offers[0]


def _ship(amount, currency="USD", country="US"):
    return {"price": {"amount": amount, "currency": currency}, "country": country, "service": "Ground"}


def test_query_requests_shipping_subfields():
    from connectors.cj import QUERY

    assert "shipping { price { amount currency } country service }" in QUERY


@pytest.mark.parametrize(
    "options, expected",
    [
        ([_ship("5.99")], "5.99"),
        ([_ship("9.99"), _ship("4.50"), _ship("7")], "4.50"),
        ([_ship("0")], "0"),
        ([_ship("0.00"), _ship("3")], "0.00"),
        ([_ship("3.00", country="")], "3.00"),
        ([_ship("1.00", country="CA"), _ship("6.00")], "6.00"),
        ([_ship("2.00", currency="CAD"), _ship("8.00")], "8.00"),
    ],
)
def test_shipping_lowest_us_usd_option(options, expected):
    assert _with_shipping(options).fields["shipping"] == expected


@pytest.mark.parametrize(
    "options",
    [
        None,
        [],
        "free",
        [_ship("1.00", country="CA")],
        [_ship("2.00", currency="CAD")],
        [_ship("abc")],
        [_ship("NaN")],
        [_ship("-3")],
        [{"country": "US"}],
        ["x", 5],
    ],
)
def test_shipping_unknown_has_no_key(options):
    assert "shipping" not in _with_shipping(options).fields
