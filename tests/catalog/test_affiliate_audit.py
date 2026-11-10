import json

import httpx

from catalog.affiliate_audit import (
    CjAdvertisers,
    Merchant,
    SovrnMerchants,
    audit,
    render_table,
    unmapped_chains,
)

SECRET = "SOVRN-SECRET-VALUE"
TOKEN = "CJ-TOKEN-VALUE"
CID = "7654321"


def sovrn_record(name="Walmart", approved=True, countries="us", domains=("walmart.com",), **extra):
    rec = {"id": 1, "groupId": 1, "name": name, "groupName": name, "approved": approved,
           "countries": countries, "insider": False,
           "rates": [{"min": 1.0, "max": 4.0, "type": "percent", "description": "Grocery 1%", "typeName": "CPA"}]}
    if domains is not None:
        rec["domains"] = list(domains)
    rec.update(extra)
    return rec


def sovrn_client(by_domain, calls=None, status=200):
    def handler(request):
        if calls is not None:
            calls.append(request)
        assert request.headers["Authorization"] == f"secret {SECRET}"
        domain = request.url.params["domain"]
        if status != 200:
            return httpx.Response(status, text="nope")
        return httpx.Response(200, json={"page": 1, "totalPages": 1, "merchants": by_domain.get(domain, [])})
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_sovrn(by_domain, sleeps=None, calls=None, status=200):
    sleeps = sleeps if sleeps is not None else []
    return SovrnMerchants(sovrn_client(by_domain, calls, status), SECRET, sleep=sleeps.append)


def lookup(by_domain, domain="walmart.com"):
    return make_sovrn(by_domain).lookup(domain)


def test_sovrn_approved_with_rate():
    r = lookup({"walmart.com": [sovrn_record()]})
    assert r.status == "approved"
    assert r.name == "Walmart"
    assert "Grocery 1%" in r.rate and "1.0" in r.rate and "4.0" in r.rate


def test_sovrn_not_approved():
    r = lookup({"walmart.com": [sovrn_record(approved=False)]})
    assert r.status == "not approved"


def test_sovrn_none_when_no_records():
    assert lookup({}).status == "none"


def test_sovrn_loosely_related_approved_record_does_not_count():
    other = sovrn_record(name="walmart - 262", domains=["walmart-photo.com"])
    r = lookup({"walmart.com": [other]})
    assert r.status == "none"


def test_sovrn_related_approved_ignored_in_favor_of_real_unapproved():
    other = sovrn_record(name="walmart - 262", domains=["walmart-photo.com"])
    real = sovrn_record(approved=False)
    assert lookup({"walmart.com": [other, real]}).status == "not approved"


def test_sovrn_www_and_case_insensitive_domain_match():
    r = lookup({"walmart.com": [sovrn_record(domains=["WWW.Walmart.com"])]})
    assert r.status == "approved"


def test_sovrn_countries():
    assert lookup({"walmart.com": [sovrn_record(countries="all,pr")]}).status == "approved"
    assert lookup({"walmart.com": [sovrn_record(countries="gb,ca,us")]}).status == "approved"
    assert lookup({"walmart.com": [sovrn_record(countries="gb")]}).status == "not approved"


def test_sovrn_missing_domains_falls_back_to_name_prefix():
    ok = sovrn_record(name="Walmart US", domains=None)
    assert lookup({"walmart.com": [ok]}).status == "approved"
    bad = sovrn_record(name="Other Store", groupName="Other Store", domains=None)
    assert lookup({"walmart.com": [bad]}).status == "none"


def test_sovrn_sleeps_between_calls_only():
    sleeps = []
    s = make_sovrn({}, sleeps)
    s.lookup("a.com")
    assert sleeps == []
    s.lookup("b.com")
    s.lookup("c.com")
    assert len(sleeps) == 2 and all(d >= 3.1 for d in sleeps)


def test_sovrn_http_error_reports_type_and_status_only_then_stops():
    calls = []
    s = make_sovrn({}, calls=calls, status=401)
    r1 = s.lookup("a.com")
    r2 = s.lookup("b.com")
    assert r1.status == "error" and r2.status == "unavailable"
    assert len(calls) == 1
    assert "HTTPStatusError" in s.unavailable and "401" in s.unavailable
    assert SECRET not in s.unavailable


def cj_xml(advertisers, total=None):
    items = ""
    for a in advertisers:
        items += (
            f"<advertiser><advertiser-id>{a['id']}</advertiser-id>"
            f"<advertiser-name>{a['name']}</advertiser-name><program-url>{a['url']}</program-url>"
            f"<relationship-status>{a['rel']}</relationship-status>"
            f"<three-month-epc>{a.get('epc', '12.3')}</three-month-epc>"
            f"<actions><action><name>Sale</name><type>advertiser</type>"
            f"<commission><default>{a.get('comm', 'USD 0.50')}</default></commission></action></actions>"
            f"</advertiser>"
        )
    total = len(advertisers) if total is None else total
    return (f'<cj-api><advertisers total-matched="{total}" records-returned="{len(advertisers)}" '
            f'page-number="1">{items}</advertisers></cj-api>')


def cj_error(msg=f"User is not authorized to access this API on behalf of this CID: {CID}"):
    return httpx.Response(400, text=f"<cj-api><error-message>{msg}</error-message></cj-api>")


def make_cj(handler, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return CjAdvertisers(httpx.Client(transport=httpx.MockTransport(handler)), TOKEN, CID, sleep=sleeps.append)


def cj_handler(joined, notjoined, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        p = request.url.params
        assert p["requestor-cid"] == CID
        if p["advertiser-ids"] == "joined":
            return httpx.Response(200, text=cj_xml(joined))
        assert p["advertiser-ids"] == "notjoined"
        name = p["advertiser-name"]
        return httpx.Response(200, text=cj_xml(notjoined.get(name, [])))
    return handler


COSTCO = {"id": 1, "name": "Costco", "url": "https://www.costco.com/path", "rel": "joined"}
TARGET = {"id": 2, "name": "Target", "url": "https://target.com", "rel": "notjoined", "comm": "USD 1.25", "epc": "8.1"}


def test_cj_joined_notjoined_none():
    cj = make_cj(cj_handler([COSTCO], {"target.com": [TARGET]}))
    assert cj.lookup("costco.com").status == "joined"
    t = cj.lookup("target.com")
    assert t.status == "available" and "USD 1.25" in t.detail and "8.1" in t.detail
    assert cj.lookup("nowhere.com").status == "none"


def test_cj_notjoined_hit_must_match_domain():
    unrelated = {"id": 9, "name": "Other", "url": "https://other-target.com", "rel": "notjoined"}
    cj = make_cj(cj_handler([], {"target.com": [unrelated]}))
    assert cj.lookup("target.com").status == "none"


def test_cj_joined_paginates(monkeypatch):
    monkeypatch.setattr("catalog.affiliate_audit.CJ_PAGE_SIZE", 1)
    pages = []

    def handler(request):
        p = request.url.params
        if p["advertiser-ids"] != "joined":
            return httpx.Response(200, text=cj_xml([]))
        pages.append(p["page-number"])
        if p["page-number"] == "1":
            return httpx.Response(200, text=cj_xml([COSTCO], total=2))
        return httpx.Response(200, text=cj_xml([dict(COSTCO, id=3, url="https://aldi.us")], total=2))

    cj = make_cj(handler)
    assert cj.lookup("aldi.us").status == "joined"
    assert pages == ["1", "2"]


def test_cj_auth_error_masks_cid_and_stops_calls():
    calls = []

    def handler(request):
        calls.append(request)
        return cj_error()

    cj = make_cj(handler)
    assert cj.lookup("a.com").status == "unavailable"
    assert cj.lookup("b.com").status == "unavailable"
    assert len(calls) == 1
    assert CID not in cj.unavailable and "<cid>" in cj.unavailable
    assert TOKEN not in cj.unavailable


def test_cj_http_failure_reports_type_and_status():
    cj = make_cj(lambda r: httpx.Response(503, text="boom"))
    cj.lookup("a.com")
    assert "503" in cj.unavailable and "HTTPStatusError" in cj.unavailable


def test_cj_sleeps_between_calls():
    sleeps = []
    cj = make_cj(cj_handler([COSTCO], {}), sleeps)
    cj.lookup("costco.com")  # joined call only
    cj.lookup("a.com")
    cj.lookup("b.com")
    assert len(sleeps) == 2 and all(d >= 2.5 for d in sleeps)


def merchants():
    return [
        Merchant("Walmart", ["op-walmart", "walmart"], "walmart.com"),
        Merchant("Costco", ["op-costco"], "costco.com"),
        Merchant("Target", ["op-target"], "target.com"),
        Merchant("Mystery", ["op-mystery"], "mystery.example"),
        Merchant("Nodomain", ["op-nodomain"], None),
    ]


COUNTS = {
    "offers": {"op-walmart": 5, "walmart": 3, "op-costco": 20, "op-target": 1, "op-mystery": 1, "op-stray": 4, "amazon": 2},
    "clicks": {"walmart": 7, "op-costco": 1},
}


def test_audit_actions_and_sorting():
    sovrn = make_sovrn({
        "walmart.com": [sovrn_record()],
        "costco.com": [sovrn_record(name="Costco", domains=["costco.com"])],
    })
    cj = make_cj(cj_handler([COSTCO], {"target.com": [TARGET]}))
    rows = audit(merchants(), COUNTS, sovrn, cj)
    assert [r.name for r in rows] == ["Costco", "Walmart", "Mystery", "Target", "Nodomain"]
    by = {r.name: r for r in rows}
    assert by["Walmart"].offers == 8 and by["Walmart"].clicks == 7
    assert by["Walmart"].action == "covered (Sovrn)"
    assert by["Costco"].action == "joined (CJ) — prefer over Sovrn"
    assert by["Target"].action == "apply on CJ"
    assert by["Mystery"].action == "no program found"
    assert by["Nodomain"].action == "no domain"
    assert by["Nodomain"].sovrn == "—"


def test_audit_sort_order_offers_then_clicks_then_name():
    ms = [Merchant("B", ["b"], None), Merchant("A", ["a"], None), Merchant("C", ["c"], None)]
    counts = {"offers": {"a": 1, "b": 1, "c": 2}, "clicks": {"a": 0, "b": 5}}
    assert [r.name for r in audit(ms, counts, None, None)] == ["C", "B", "A"]


def test_audit_not_configured_makes_no_requests():
    rows = audit(merchants(), COUNTS, None, None)
    assert all(r.sovrn == "not configured" and r.cj == "not configured" for r in rows if r.domain)
    assert by_name(rows, "Walmart").action == "no program found"


def by_name(rows, name):
    return next(r for r in rows if r.name == name)


def test_audit_cj_unavailable_column_dash():
    cj = make_cj(lambda r: cj_error())
    rows = audit(merchants(), COUNTS, None, cj)
    assert all(r.cj == "—" for r in rows if r.domain)
    assert cj.unavailable


def test_unmapped_chains():
    out = unmapped_chains(merchants(), COUNTS["offers"])
    assert out == [("stray", 4)]


def test_render_table_and_json_shape():
    rows = audit(merchants()[:1], COUNTS, None, None)
    text = render_table(rows)
    lines = text.splitlines()
    assert lines[0].split("|")[0].strip() == "Merchant"
    assert "Walmart" in text and "walmart.com" in text
    assert json.loads(json.dumps([r.as_dict() for r in rows]))[0]["name"] == "Walmart"
