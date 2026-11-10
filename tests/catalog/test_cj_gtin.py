"""CJ barcode lookups for products we already show (#111). No network."""

import copy
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from django.core.management import call_command

from catalog import cj_gtin, scheduler
from catalog.budget import DbBudget
from catalog.cj_gtin import CONNECTOR, backfill, candidates, run_backfill
from catalog.models import CjGtinCheck, IngestionRun, Offer, Product, SourceBudget
from connectors.base import ConnectorError
from connectors.cj import CjConnector
from connectors.live import BudgetExhausted, Stopping
from tests.catalog.test_observed import NOW, loc, obs, sync

RECORDINGS = Path(__file__).resolve().parents[1] / "connectors" / "recordings" / "cj"
GTIN = "0860010924006"  # observed UPCs are zero-padded to 13 digits
TOKEN = "tok-SECRET"
CID = "9999999"


@pytest.fixture(autouse=True)
def cats(db, settings):
    from catalog.categories_sync import sync_categories

    sync_categories(settings.CATEGORIES_DIR)


def _joined_body():
    body = json.loads((RECORDINGS / "gtin_860010924006_notjoined.json").read_text())["body"]
    body = copy.deepcopy(body)
    for item in body["data"]["shoppingProducts"]["resultList"]:
        item["joinedStatus"] = True
    return body


def connector(responder=None, joined=True):
    body = _joined_body()
    if not joined:
        body = json.loads((RECORDINGS / "gtin_860010924006_notjoined.json").read_text())["body"]
    seen = []

    def handler(request):
        seen.append(request)
        if responder is not None:
            return responder(request)
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    c = CjConnector(TOKEN, CID, client=client, clock=lambda: NOW)
    c.seen = seen
    return c


class FakeBudget:
    def __init__(self, tokens=None):
        self.tokens = tokens
        self.waits = []
        self.reports = []

    def wait(self, key):
        if self.tokens is not None:
            if self.tokens == 0:
                raise BudgetExhausted(3600.0)
            self.tokens -= 1
        self.waits.append(key)

    def report(self, key, status, retry_after=None):
        self.reports.append((key, status))


class FakeConnector:
    """Records the barcodes asked; answers from a dict (an exception instance is raised)."""

    def __init__(self, answers=None, joined=True):
        self.answers = answers or {}
        self.asked = []
        self.joined = joined
        self.joined_checks = 0

    def has_joined_advertisers(self, budget=None):
        self.joined_checks += 1
        if budget is not None:
            budget.acquire("cj")
            budget.report("cj", 200, None)
        if isinstance(self.joined, Exception):
            raise self.joined
        return self.joined

    def lookup_gtin(self, gtin, budget=None):
        from connectors.base import SearchResult

        self.asked.append(gtin)
        if budget is not None:
            budget.acquire("cj")
            budget.report("cj", 200, None)
        out = self.answers.get(gtin, SearchResult([], 0))
        if isinstance(out, Exception):
            raise out
        return out


def observe(*specs):
    """specs: (upc, title, days_old). Each at its own store."""
    sync(
        *[
            obs(price_id=i, upc=upc, title=title, days=days, location=loc(osm_id=i))
            for i, (upc, title, days) in enumerate(specs, start=1)
        ]
    )


def test_candidate_order_curated_first_then_newest():
    observe(
        ("0000000000017", "Zzqx gizmo widget", 1),  # uncurated, newest
        (GTIN, "Whole milk 1 gal", 10),  # curated
        ("0000000000024", "Whole milk 1 gal", 3),  # curated, newer
    )
    # make curated-ness explicit instead of relying on the matcher
    Offer.objects.filter(retailer_item_id__startswith="0000000000017@").update(category=None)
    assert Offer.objects.filter(retailer_item_id__startswith=f"{GTIN}@", category__isnull=False).exists()
    assert candidates(NOW, 10) == ["0000000000024", GTIN, "0000000000017"]
    assert candidates(NOW, 2) == ["0000000000024", GTIN]


def test_recheck_intervals_are_named():
    assert cj_gtin.FOUND_RECHECK_AFTER == timedelta(hours=20)
    assert cj_gtin.EMPTY_RECHECK_AFTER == timedelta(days=7)


def test_empty_check_waits_seven_days():
    observe((GTIN, "Whole milk 1 gal", 1), ("0000000000024", "Whole milk 1 gal", 2))
    CjGtinCheck.objects.create(upc=GTIN, checked_at=NOW - timedelta(days=6, hours=23), found=0)
    assert candidates(NOW, 10) == ["0000000000024"]
    CjGtinCheck.objects.filter(pk=GTIN).update(checked_at=NOW - timedelta(days=7, minutes=1))
    assert set(candidates(NOW, 10)) == {GTIN, "0000000000024"}


def test_found_check_is_due_after_twenty_hours():
    observe((GTIN, "Whole milk 1 gal", 1), ("0000000000024", "Whole milk 1 gal", 2))
    CjGtinCheck.objects.create(upc=GTIN, checked_at=NOW - timedelta(hours=19, minutes=59), found=2)
    assert candidates(NOW, 10) == ["0000000000024"]
    CjGtinCheck.objects.filter(pk=GTIN).update(checked_at=NOW - timedelta(hours=20, minutes=1))
    assert set(candidates(NOW, 10)) == {GTIN, "0000000000024"}
    # the next daily run picks a found barcode up again
    CjGtinCheck.objects.filter(pk=GTIN).update(checked_at=NOW - timedelta(days=1))
    assert GTIN in candidates(NOW, 10)


def test_stale_observed_offers_are_not_candidates(settings):
    observe((GTIN, "Whole milk 1 gal", 1))
    Offer.objects.update(observed_on=(NOW - settings.OBSERVED_MAX_AGE - timedelta(days=1)).date())
    assert candidates(NOW, 10) == []


def test_distinct_upcs_across_stores():
    sync(
        obs(price_id=1, upc=GTIN, location=loc(osm_id=1)),
        obs(price_id=2, upc=GTIN, location=loc(osm_id=2)),
    )
    assert candidates(NOW, 10) == [GTIN]


def test_written_offer_is_cj_and_links_to_existing_product():
    observe((GTIN, "Thrive Market organic thing", 1))
    product = Offer.objects.get(retailer_item_id__startswith=f"{GTIN}@").product
    c = connector()
    stats = backfill(c, FakeBudget(), NOW, 10)
    cj = Offer.objects.filter(retailer__startswith="cj-")
    assert cj.count() >= 1
    assert {o.product_id for o in cj} == {product.pk}
    assert {o.kind for o in cj} == {Offer.Kind.LIVE}
    assert Product.objects.count() == 1
    check = CjGtinCheck.objects.get(pk=GTIN)
    assert check.found == len(connector().lookup_gtin(GTIN).offers) >= 1 and check.checked_at == NOW
    assert (stats.looked_up, stats.found, stats.offers) == (1, 1, check.found)
    assert stats.stopped == "" and stats.errors == 0
    assert TOKEN not in repr(stats) and CID not in stats.line()


def test_not_joined_makes_no_offer_but_marks_checked():
    observe((GTIN, "Thrive Market organic thing", 1))
    stats = backfill(connector(joined=False), FakeBudget(), NOW, 10)
    assert Offer.objects.filter(retailer__startswith="cj-").count() == 0
    assert CjGtinCheck.objects.get(pk=GTIN).found == 0
    assert (stats.looked_up, stats.found, stats.offers) == (1, 0, 0)


def test_budget_is_drawn_per_request():
    observe((GTIN, "Thrive Market organic thing", 1))
    budget = FakeBudget()
    backfill(connector(), budget, NOW, 10)
    assert budget.waits and set(budget.waits) == {"cj"}
    assert [k for k, _ in budget.reports] == ["cj"] * len(budget.waits)


def test_connector_error_stops_run_and_does_not_mark_checked():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector({"0000000000024": ConnectorError("boom")})
    stats = backfill(fake, FakeBudget(), NOW, 10)
    assert fake.asked == ["0000000000024"]  # stopped at the first failure
    assert CjGtinCheck.objects.count() == 0
    assert stats.errors == 1 and stats.stopped == "ConnectorError"


def test_input_specific_error_marks_checked_and_continues():
    from connectors.cj import CjRequestError

    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector({"0000000000024": CjRequestError("CJ search failed with HTTP 400", retryable=False)})
    stats = backfill(fake, FakeBudget(), NOW, 10)
    assert fake.asked == ["0000000000024", GTIN]
    check = CjGtinCheck.objects.get(pk="0000000000024")
    assert (check.found, check.checked_at) == (0, NOW)
    assert CjGtinCheck.objects.filter(pk=GTIN).exists()
    assert stats.errors == 1 and stats.stopped == ""


@pytest.mark.parametrize(
    "resp",
    [httpx.Response(400, text=TOKEN), httpx.Response(200, json={"errors": [{"message": CID}], "data": None})],
)
def test_http_400_or_graphql_errors_do_not_stall_the_queue(resp, caplog):
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    c = connector(lambda r: resp)
    caplog.set_level(logging.DEBUG)
    stats = backfill(c, FakeBudget(), NOW, 10)
    assert set(CjGtinCheck.objects.values_list("pk", "found")) == {("0000000000024", 0), (GTIN, 0)}
    assert stats.errors == 2 and stats.stopped == "" and stats.looked_up == 0
    assert TOKEN not in caplog.text and CID not in caplog.text


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_http_errors_stop_without_marking(status):
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    c = connector(lambda r: httpx.Response(status))
    stats = backfill(c, FakeBudget(), NOW, 10)
    assert CjGtinCheck.objects.count() == 0 and stats.errors == 1
    assert stats.stopped == "CjRequestError" and len(c.seen) == 1


def test_transport_error_stops_without_marking():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))

    def boom(request):
        raise httpx.ConnectError(TOKEN)

    stats = backfill(connector(boom), FakeBudget(), NOW, 10)
    assert CjGtinCheck.objects.count() == 0 and stats.errors == 1 and stats.stopped


def test_time_cap_ends_run_cleanly():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2), ("0000000000031", "Whole milk 1 gal", 3))
    assert cj_gtin.CJ_GTIN_MAX_SECONDS == 600
    ticks = iter([0.0, 0.0, 599.9, 600.0, 10_000.0])
    fake = FakeConnector()
    stats = backfill(fake, FakeBudget(), NOW, 10, clock=lambda: next(ticks))
    assert len(fake.asked) == 2 and stats.stopped == "time cap"
    assert CjGtinCheck.objects.count() == 2


def test_run_backfill_time_cap_is_succeeded():
    observe((GTIN, "Whole milk 1 gal", 1))
    ticks = iter([0.0, 700.0, 700.0])
    run, stats = run_backfill(FakeConnector(), FakeBudget(), NOW, 10, clock=lambda: next(ticks))
    assert run.status == "succeeded" and run.error == "time cap" and stats.looked_up == 0


def test_run_backfill_skips_without_joined_advertisers():
    observe((GTIN, "Whole milk 1 gal", 1))
    fake = FakeConnector(joined=False)
    budget = FakeBudget()
    run, _ = run_backfill(fake, budget, NOW, 10)
    assert fake.joined_checks == 1 and fake.asked == []
    assert budget.waits == ["cj"]  # the one check request is budgeted
    assert (run.connector, run.status, run.fetched, run.error) == (CONNECTOR, "succeeded", 0, "no joined advertisers")
    assert CjGtinCheck.objects.count() == 0


def test_run_backfill_checks_joined_then_looks_up():
    observe((GTIN, "Whole milk 1 gal", 1))
    fake = FakeConnector()
    run, _ = run_backfill(fake, FakeBudget(), NOW, 10)
    assert fake.joined_checks == 1 and fake.asked == [GTIN] and run.fetched == 1


def test_has_joined_check_uses_real_connector_request():
    observe((GTIN, "Whole milk 1 gal", 1))
    c = connector()
    run, _ = run_backfill(c, FakeBudget(), NOW, 10)
    first = json.loads(c.seen[0].content)["variables"]
    assert first["limit"] == 1 and first.get("gtin") is None
    assert run.status == "succeeded" and run.fetched == 1


def test_run_backfill_joined_check_error_is_failed():
    observe((GTIN, "Whole milk 1 gal", 1))
    fake = FakeConnector(joined=ConnectorError(f"x {TOKEN}"))
    run, _ = run_backfill(fake, FakeBudget(), NOW, 10)
    assert run.status == "failed" and run.error == "ConnectorError" and fake.asked == []


def test_run_backfill_joined_check_budget_exhausted_is_succeeded():
    observe((GTIN, "Whole milk 1 gal", 1))
    fake = FakeConnector()
    run, _ = run_backfill(fake, FakeBudget(tokens=0), NOW, 10)
    assert run.status == "succeeded" and run.error == "budget exhausted" and fake.asked == []


def test_default_limit_is_200():
    assert cj_gtin.DEFAULT_LIMIT == 200


def test_http_error_response_marks_nothing(caplog):
    observe((GTIN, "Whole milk 1 gal", 1))
    c = connector(lambda r: httpx.Response(500, text=f"{TOKEN}"))
    caplog.set_level(logging.DEBUG)
    stats = backfill(c, FakeBudget(), NOW, 10)
    assert CjGtinCheck.objects.count() == 0 and stats.errors == 1
    assert TOKEN not in caplog.text and TOKEN not in stats.line()


def test_budget_exhausted_ends_cleanly():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector()
    stats = backfill(fake, FakeBudget(tokens=1), NOW, 10)
    assert len(fake.asked) == 2 and stats.stopped == "budget exhausted"
    assert CjGtinCheck.objects.count() == 1  # the second was never marked
    assert stats.errors == 0


def test_stopping_ends_run_before_next_lookup():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector()
    flags = iter([False, True])
    stats = backfill(fake, FakeBudget(), NOW, 10, stopping=lambda: next(flags))
    assert len(fake.asked) == 1 and stats.stopped == "interrupted"


def test_stopping_raised_by_budget_is_clean():
    observe((GTIN, "Whole milk 1 gal", 1))

    class Stop(FakeBudget):
        def wait(self, key):
            raise Stopping()

    stats = backfill(connector(), Stop(), NOW, 10)
    assert stats.stopped == "interrupted" and CjGtinCheck.objects.count() == 0


def test_limit_caps_lookups():
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector()
    stats = backfill(fake, FakeBudget(), NOW, 1)
    assert len(fake.asked) == 1 and stats.looked_up == 1


def test_run_backfill_records_ingestion_run():
    observe((GTIN, "Thrive Market organic thing", 1))
    run, stats = run_backfill(connector(), FakeBudget(), NOW, 10)
    assert (run.connector, run.category_slug, run.status, run.error) == (CONNECTOR, "all", "succeeded", "")
    assert CONNECTOR == "cj-gtin" and run.fetched == 1
    assert IngestionRun.objects.filter(connector="cj-gtin").count() == 1


def test_run_backfill_error_is_failed_with_type_name_only():
    observe((GTIN, "Whole milk 1 gal", 1))
    run, _ = run_backfill(FakeConnector({GTIN: ConnectorError(f"bad {TOKEN}")}), FakeBudget(), NOW, 10)
    assert run.status == "failed" and run.error == "ConnectorError" and TOKEN not in run.error


def test_run_backfill_budget_exhausted_is_succeeded():
    observe((GTIN, "Whole milk 1 gal", 1))
    run, _ = run_backfill(FakeConnector(), FakeBudget(tokens=0), NOW, 10)
    assert run.status == "succeeded" and run.error == "budget exhausted"


def test_run_backfill_unexpected_error_never_raises(monkeypatch):
    monkeypatch.setattr(cj_gtin, "candidates", lambda *a: 1 / 0)
    run, _ = run_backfill(FakeConnector(), FakeBudget(), NOW, 10)
    assert run.status == "failed" and run.error == "ZeroDivisionError"


# scheduler


def _open_prices_enabled(settings, monkeypatch):
    class FakeOP:
        unparsed = 0

        def __init__(self, *a, **k):
            pass

        def fetch_window(self, since, currency):
            return []

        def close(self):
            pass

    settings.OPEN_PRICES_ENABLED = True
    monkeypatch.setattr(scheduler, "OpenPricesClient", FakeOP)
    monkeypatch.setattr(scheduler, "due", lambda now, connectors: [])
    monkeypatch.setattr(scheduler.observed_off, "backfill_sizes", lambda *a, **k: (_off_run(), None))


def _off_run():
    return IngestionRun.objects.create(
        connector="off-product", category_slug="all", started_at=NOW, status="succeeded"
    )


def _budget():
    SourceBudget.objects.create(
        source="cj", capacity=5, refill_per_second=1, tokens=5, refilled_at=NOW, day=NOW.date()
    )
    return DbBudget(now=lambda: NOW, sleep=lambda s: None)


def test_scheduler_runs_cj_gtin_only_when_cj_available(settings, monkeypatch, caplog):
    _open_prices_enabled(settings, monkeypatch)
    calls = []

    def fake_run(conn, budget, now, limit, stopping=None):
        calls.append((conn, limit))
        return IngestionRun.objects.create(
            connector=CONNECTOR, category_slug="all", started_at=now, status="succeeded"
        ), None

    monkeypatch.setattr(scheduler.cj_gtin, "run_backfill", fake_run)
    budget = _budget()
    marker = object()

    runs = scheduler.tick(NOW, {}, budget=budget)
    assert [r.connector for r in runs] == ["openprices", "off-product"] and calls == []

    IngestionRun.objects.all().delete()
    caplog.set_level(logging.INFO)
    runs = scheduler.tick(NOW, {"cj": marker}, budget=budget)
    assert [r.connector for r in runs] == ["openprices", "off-product", "cj-gtin"]
    assert calls == [(marker, 200)]
    assert "ingest connector=cj-gtin category=all status=succeeded" in caplog.text


def test_scheduler_cj_gtin_error_is_isolated(settings, monkeypatch):
    _open_prices_enabled(settings, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(scheduler.cj_gtin, "run_backfill", boom)
    runs = scheduler.tick(NOW, {"cj": object()}, budget=_budget())
    last = runs[-1]
    assert (last.connector, last.status) == ("cj-gtin", "failed")
    assert TOKEN not in last.error


# command


def test_command_runs_with_limit(monkeypatch):
    observe(("0000000000024", "Whole milk 1 gal", 1), (GTIN, "Whole milk 1 gal", 2))
    fake = FakeConnector()
    monkeypatch.setattr("connectors.registry.available", lambda *a, **k: {"cj": fake})
    out = io.StringIO()
    call_command("cj_gtin_lookup", "--limit", "1", stdout=out)
    assert len(fake.asked) == 1 and "status=succeeded" in out.getvalue()


def test_command_without_cj_names_env_vars(monkeypatch):
    from django.core.management.base import CommandError

    monkeypatch.setattr("connectors.registry.available", lambda *a, **k: {})
    with pytest.raises(CommandError, match="CJ_PERSONAL_ACCESS_TOKEN"):
        call_command("cj_gtin_lookup", stdout=io.StringIO())
