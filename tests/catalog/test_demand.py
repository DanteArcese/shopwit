import re
from datetime import date, timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from catalog import demand
from catalog.models import FetchJob, SearchDemand, SourceBudget
from connectors.live import SourceInfo
from tests.catalog.helpers import NOW
from tests.catalog.test_generic_match import make_generic_offer

TODAY = NOW.date()
WALMART = SourceInfo("walmart", ("walmart",), timedelta(hours=12))
OFF = SourceInfo("off-prices", ("off-search", "openprices"), timedelta(hours=24))
SOURCES = {"walmart": WALMART, "off-prices": OFF}

pytestmark = pytest.mark.django_db


def row(phrase, *, days=1, count=1, last=TODAY):
    return SearchDemand.objects.create(
        phrase=phrase, count=count, days_seen=days, last_seen=last
    )


@pytest.fixture
def budgets():
    for key in ("walmart", "off-search", "openprices"):
        SourceBudget.objects.create(
            source=key, capacity=5.0, refill_per_second=1.0, tokens=5.0,
            refilled_at=NOW, daily_limit=None, daily_used=0, day=NOW.date(),
        )


@pytest.fixture
def web(client, monkeypatch, budgets):
    monkeypatch.setattr("web.views.now", lambda: NOW)
    monkeypatch.setattr("web.search.live_source_info", lambda **kw: {"walmart": WALMART})
    return client


def test_counts_once_per_render_never_per_poll(web):
    web.get("/search", {"q": "oat milk"})
    assert SearchDemand.objects.get(phrase="oat milk").count == 1
    start = int(NOW.timestamp())
    for _ in range(3):
        web.get(
            "/search", {"q": "oat milk", "poll": start, "seen": start},
            HTTP_HX_REQUEST="true",
        )
    assert SearchDemand.objects.get(phrase="oat milk").count == 1
    web.get("/search", {"q": "oat milk"}, HTTP_HX_REQUEST="true")
    web.get("/search", {"q": "oat milk"}, HTTP_USER_AGENT="Googlebot/2.1 bot")
    assert SearchDemand.objects.get(phrase="oat milk").count == 1


def test_curated_redirect_and_personal_are_not_counted(web):
    from catalog.categories_sync import sync_categories

    sync_categories(settings.CATEGORIES_DIR)
    resp = web.get("/search", {"q": "milk"})
    assert resp.status_code == 302
    web.get("/search", {"q": "call 555-123-4567"})
    names = set(SearchDemand.objects.values_list("phrase", flat=True))
    assert "milk" not in names
    assert not any("555" in n for n in names)


def test_personal_phrase_is_a_noop():
    demand.record_demand("555-123-4567", TODAY)
    demand.record_demand("", TODAY)
    assert SearchDemand.objects.count() == 0


def test_days_seen_once_per_day():
    day1, day2 = TODAY, TODAY + timedelta(days=1)
    demand.record_demand("oat milk", day1)
    demand.record_demand("oat milk", day1)
    demand.record_demand("oat milk", day2)
    r = SearchDemand.objects.get(phrase="oat milk")
    assert (r.count, r.days_seen, r.last_seen) == (3, 2, day2)
    demand.record_demand("oat milk", day1)  # an older date never rewinds last_seen
    r.refresh_from_db()
    assert (r.count, r.days_seen, r.last_seen) == (4, 2, day2)


def test_prune_thresholds(monkeypatch):
    row("stale", days=2, last=TODAY - timedelta(days=31))
    row("recent", days=2, last=TODAY - timedelta(days=29))
    row("curated", days=3, last=TODAY - timedelta(days=60))
    assert demand.prune_demand(TODAY) == 1
    assert set(SearchDemand.objects.values_list("phrase", flat=True)) == {"recent", "curated"}
    monkeypatch.setattr(demand, "MAX_PHRASES", 2)
    row("lowest", days=1, last=TODAY)
    assert demand.prune_demand(TODAY) == 1
    assert set(SearchDemand.objects.values_list("phrase", flat=True)) == {"recent", "curated"}


def test_admin_hides_under_three_days(client, monkeypatch):
    get_user_model().objects.create_superuser("root", "r@example.com", "pw")
    client.login(username="root", password="pw")
    row("oat milk", days=3, count=9)
    row("rare thing", days=2, count=50)
    make_generic_offer("Oat Milk A", "3", dim="volume", amount=1000, path="Food/Dairy")
    make_generic_offer("Oat Milk B", "3", dim="volume", amount=1000, path="Food/Dairy")
    make_generic_offer("Oat Milk C", "3", dim="volume", amount=1000, path="Food/Other")
    import catalog.admin as admin_mod

    monkeypatch.setattr(admin_mod, "_now", lambda: NOW)
    resp = client.get("/admin/catalog/curatenext/")
    body = resp.content.decode()
    assert resp.status_code == 200
    assert "oat milk" in body and "rare thing" not in body
    assert "Food/Dairy" in body
    assert re.search(r"field-held_results[^>]*>\s*3\s*<", body)
    assert client.get("/admin/catalog/searchdemand/").status_code == 404


def test_warm_round_top_50(budgets):
    for n in range(60):
        row(f"phrase {n:02d}", days=3, count=1000 - n)
    row("call 555-123-4567", days=9, count=99999)
    created = demand.warm_round(NOW, SOURCES)
    assert created == 100
    jobs = FetchJob.objects.filter(origin="warm")
    assert jobs.filter(source="walmart").count() == 50
    assert jobs.filter(source="off-prices").count() == 50
    assert not jobs.filter(phrase__contains="555").exists()
    first = list(jobs.filter(source="walmart").order_by("id").values_list("phrase", flat=True))
    assert first == [f"phrase {n:02d}" for n in range(50)]


def test_warm_round_skips_recent_success(budgets):
    row("fresh one", days=3, count=10)
    row("other", days=3, count=5)
    FetchJob.objects.create(
        source="walmart", phrase="fresh one", status="succeeded",
        not_before=NOW, finished_at=NOW - timedelta(hours=1),
    )
    demand.warm_round(NOW, {"walmart": WALMART})
    assert list(FetchJob.objects.filter(origin="warm").values_list("phrase", flat=True)) == ["other"]


def test_warm_due_every_12h(budgets):
    assert not demand.warm_due(NOW)  # no demand, no jobs
    row("oat milk", days=3, count=5)
    assert demand.warm_due(NOW)
    demand.warm_round(NOW, {"walmart": WALMART})
    assert not demand.warm_due(NOW + timedelta(hours=11))
    assert demand.warm_due(NOW + timedelta(hours=12))
    first = FetchJob.objects.get(origin="warm")
    t1 = NOW + timedelta(hours=12)
    demand.warm_round(t1, {"walmart": WALMART})
    first.refresh_from_db()
    assert first.status == "expired"
    assert FetchJob.objects.filter(origin="warm", status="queued").count() == 1


def test_tick_prunes_and_warms(budgets, monkeypatch):
    from catalog import scheduler

    monkeypatch.setattr("catalog.scheduler.live_source_info", lambda **kw: {"walmart": WALMART})
    row("old", days=1, last=TODAY - timedelta(days=40))
    row("hot", days=3, count=5)
    scheduler.tick(NOW, {})
    assert not SearchDemand.objects.filter(phrase="old").exists()
    assert FetchJob.objects.filter(origin="warm", phrase="hot").count() == 1


def test_demand_rows_hold_no_personal_fields():
    assert {f.name for f in SearchDemand._meta.get_fields()} == {
        "id", "phrase", "count", "days_seen", "last_seen",
    }
    assert not [
        f.name for f in FetchJob._meta.get_fields()
        if re.search(r"ip|user|session|agent|referer", f.name)
    ]
