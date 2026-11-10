import json
from io import StringIO

import httpx
import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.management.commands import affiliate_audit as cmd
from catalog.models import ClickEvent, Offer, Product
from tests.catalog.helpers import NOW

SECRET = "SOVRN-SECRET-VALUE-CMD"
TOKEN = "CJ-TOKEN-VALUE-CMD"
CID = "1112223"


def make_offer(retailer, item):
    product = Product.objects.create(identity=f"id:{retailer}:{item}", title="Thing")
    from decimal import Decimal
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal("1.00"),
        currency="USD", url="https://example.com/", title="Thing", last_seen_at=NOW,
    )


@pytest.fixture
def requests_seen():
    return []


@pytest.fixture
def wired(monkeypatch, settings, requests_seen):
    sleeps = []

    def handler(request):
        requests_seen.append(request)
        if request.url.host == "publishers.viglink.com":
            domain = request.url.params["domain"]
            merchants = []
            if domain == "walmart.com":
                merchants = [{"name": "Walmart", "groupName": "Walmart", "approved": True, "countries": "us",
                              "domains": ["walmart.com"], "rates": [{"description": "Grocery 1%"}]}]
            return httpx.Response(200, json={"page": 1, "totalPages": 1, "merchants": merchants})
        return httpx.Response(400, text=f"<cj-api><error-message>bad CID {CID} token {TOKEN}</error-message></cj-api>")

    monkeypatch.setattr(cmd, "build_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(cmd, "sleep", sleeps.append)
    settings.SOVRN_SECRET_KEY = SECRET
    settings.CJ_PERSONAL_ACCESS_TOKEN = TOKEN
    settings.CJ_PUBLISHER_ID = CID
    return sleeps


def run(*args):
    out, err = StringIO(), StringIO()
    call_command("affiliate_audit", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@pytest.mark.django_db
def test_table_report_with_cj_unavailable_and_no_secrets(wired):
    make_offer("op-walmart", "1")
    make_offer("op-walmart", "2")
    make_offer("op-stray-chain", "3")
    ClickEvent.objects.create(retailer="op-walmart")
    out, err = run()
    assert "Walmart" in out and "covered (Sovrn)" in out
    assert out.count("CJ unavailable:") == 1
    assert "<cid>" in out
    assert "Unmapped" in out and "stray chain" in out.lower()
    for text in (out, err):
        for secret in (SECRET, TOKEN, CID):
            assert secret not in text


@pytest.mark.django_db
def test_cj_called_once_after_auth_error(wired, requests_seen):
    run("--no-sovrn")
    cj = [r for r in requests_seen if r.url.host != "publishers.viglink.com"]
    assert len(cj) == 1


@pytest.mark.django_db
def test_json_output_parses(wired):
    make_offer("op-walmart", "1")
    out, _ = run("--json", "--no-cj", "--only", "walmart.com")
    data = json.loads(out)
    assert len(data) == 1 and data[0]["name"] == "Walmart" and data[0]["offers"] == 1
    assert data[0]["sovrn"].startswith("approved")
    assert data[0]["cj"] == "skipped"


@pytest.mark.django_db
def test_only_restricts_lookups(wired, requests_seen):
    run("--only", "walmart.com", "--no-cj")
    sovrn = [r for r in requests_seen if r.url.host == "publishers.viglink.com"]
    assert [r.url.params["domain"] for r in sovrn] == ["walmart.com"]


@pytest.mark.django_db
def test_missing_credentials_not_configured_no_requests(wired, settings, requests_seen):
    settings.SOVRN_SECRET_KEY = ""
    settings.CJ_PERSONAL_ACCESS_TOKEN = None
    settings.CJ_PUBLISHER_ID = ""
    out, _ = run()
    assert requests_seen == []
    assert "not configured" in out


@pytest.mark.django_db
def test_cj_needs_both_token_and_cid(wired, settings, requests_seen):
    settings.CJ_PUBLISHER_ID = ""
    out, _ = run("--no-sovrn")
    assert requests_seen == []
    assert "not configured" in out


@pytest.mark.django_db
def test_sovrn_http_error_prints_type_and_status_only(wired, monkeypatch):
    monkeypatch.setattr(cmd, "build_client", lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, text=f"echo {SECRET}"))))
    out, err = run("--no-cj", "--only", "walmart.com")
    assert "401" in out and "HTTPStatusError" in out
    assert SECRET not in out + err


@pytest.mark.django_db
def test_command_makes_no_writes(wired):
    make_offer("op-walmart", "1")
    ClickEvent.objects.create(retailer="op-walmart")
    with CaptureQueriesContext(connection) as ctx:
        run()
    verbs = {q["sql"].lstrip().split(None, 1)[0].upper() for q in ctx.captured_queries}
    assert verbs <= {"SELECT"}
