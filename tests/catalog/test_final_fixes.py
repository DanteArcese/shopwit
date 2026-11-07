"""Whole-branch review fixes for M2."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.base import SystemCheckError

from catalog import scheduler
from catalog.categories_sync import sync_categories
from catalog.identity import product_identity
from catalog.ingest import ingest
from catalog.merge import merge_product
from catalog.models import Category, IngestionRun, Offer, Product, ReviewItem
from connectors.base import SearchResult
from tests.catalog.helpers import NOW, FakeConnector, no_off, raw

RECORDINGS = Path(__file__).parent.parent / "connectors" / "recordings" / "walmart"
UNPARSED = "Organic Valley Reduced Fat Organic 2% Milk"


@pytest.fixture
def milk(db):
    sync_categories(settings.CATEGORIES_DIR)
    return Category.objects.get(slug="dairy/milk")


def counts():
    return (Product.objects.count(), Offer.objects.count(), ReviewItem.objects.count())


# 1
def test_unexpected_exception_marks_run_failed_without_message(milk):
    conn = FakeConnector({}, error=RuntimeError("secret-token-123"))
    run = ingest(conn, milk, NOW, off_lookup=no_off)
    run.refresh_from_db()
    assert run.status == "failed" and run.error == "RuntimeError"
    assert run.finished_at is not None
    assert not IngestionRun.objects.filter(status="running").exists()


def test_refresh_all_categories_with_partial_recordings(milk, monkeypatch):
    for var in ("WALMART_CONSUMER_ID", "WALMART_PRIVATE_KEY", "WALMART_KEY_VERSION"):
        monkeypatch.delenv(var, raising=False)
    from io import StringIO

    out = StringIO()
    call_command("refresh", recordings=str(RECORDINGS), stdout=out)
    assert Product.objects.filter(category=milk).exists()
    runs = {r.category_slug: r for r in IngestionRun.objects.all()}
    assert runs["dairy/milk"].status == "succeeded"
    assert any(r.status == "failed" for r in runs.values())
    assert not IngestionRun.objects.filter(status="running").exists()
    assert len(out.getvalue().strip().splitlines()) == len(runs)
    assert "dairy/milk status=succeeded" in out.getvalue()
    before = counts()
    call_command("refresh", recordings=str(RECORDINGS), stdout=StringIO())
    assert counts() == before


def test_refresh_exits_nonzero_when_all_fail(milk, tmp_path):
    with pytest.raises(CommandError):
        call_command("refresh", recordings=str(tmp_path), category="dairy/milk")
    assert IngestionRun.objects.get().status == "failed"


# 2
def test_stale_instance_merge_preserves_manual_attributes(milk):
    ingest(
        FakeConnector({milk.search[0]: SearchResult([raw()], 0)}), milk, NOW, off_lookup=no_off
    )
    stale = Product.objects.get()
    fresh = Product.objects.get()
    fresh.manual_attributes = {"fat": "2"}
    fresh.save(update_fields=["manual_attributes"])
    merge_product(fresh, NOW)
    merge_product(stale, NOW)
    assert Product.objects.get().manual_attributes == {"fat": "2"}
    assert Product.objects.get().attributes["fat"]["source"] == "manual"


# 3, 9
@pytest.fixture
def admin_client(client, db):
    sync_categories(settings.CATEGORIES_DIR)
    user = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
    client.force_login(user)
    return client


@pytest.fixture
def product(admin_client):
    tp = Category.objects.get(slug="household/toilet-paper")
    p = Product.objects.create(identity="x:1", title="Soft TP", category=tp)
    Offer.objects.create(
        product=p, retailer="a", retailer_item_id="1", price=1, currency="USD",
        url="https://example.com/1", category=tp, title="Soft TP", last_seen_at=NOW,
    )
    ReviewItem.objects.create(product=p, detail="attribute:ply")
    return p


def test_offer_admin_is_read_only(admin_client, product):
    offer = product.offers.get()
    assert admin_client.get(f"/admin/catalog/offer/{offer.pk}/change/").status_code == 200
    r = admin_client.post(f"/admin/catalog/offer/{offer.pk}/change/", {"title": "Hacked", "price": "9"})
    assert r.status_code == 403
    offer.refresh_from_db()
    assert offer.title == "Soft TP"
    assert admin_client.get("/admin/catalog/offer/add/").status_code == 403


def test_product_and_reviewitem_cannot_be_added(admin_client, product):
    assert admin_client.get("/admin/catalog/product/add/").status_code == 403
    assert admin_client.get("/admin/catalog/reviewitem/add/").status_code == 403


def test_reviewitem_fields_read_only_and_mark_resolved_sets_updated_at(admin_client, product):
    item = ReviewItem.objects.get()
    before = item.updated_at
    r = admin_client.post(
        f"/admin/catalog/reviewitem/{item.pk}/change/",
        {"detail": "x", "status": "resolved", "product": product.pk},
    )
    item.refresh_from_db()
    assert item.status == "open" and item.detail == "attribute:ply"
    admin_client.post(
        "/admin/catalog/reviewitem/", {"action": "mark_resolved", "_selected_action": [item.pk]}
    )
    item.refresh_from_db()
    assert item.status == "resolved" and item.updated_at > before


# 4
def test_offer_moving_products_resolves_old_product_items(milk):
    old = FakeConnector({milk.search[0]: SearchResult([raw(title=UNPARSED, brand=None)], 0)})
    ingest(old, milk, NOW, off_lookup=no_off)
    old_p = Product.objects.get(identity="fake:A1")
    assert old_p.review_items.filter(status="open").exists()
    moved = FakeConnector(
        {milk.search[0]: SearchResult([raw(title=UNPARSED, brand=None, upc="036000291452")], 0)}
    )
    ingest(moved, milk, NOW + timedelta(hours=1), off_lookup=no_off)
    assert old_p.offers.count() == 0
    assert not old_p.review_items.filter(status="open").exists()
    assert old_p.review_items.filter(status="resolved", resolved_at__isnull=False).exists()
    assert Product.objects.get(identity=product_identity(raw(upc="036000291452"))).review_items.filter(status="open").exists()


# 7
def test_run_loop_builds_connectors_once_and_closes(cats_db, monkeypatch):
    closed, built = [], []

    class C(FakeConnector):
        def close(self):
            closed.append(1)

    def available(*a, **k):
        built.append(1)
        return {"fake": C({})}

    monkeypatch.setattr(scheduler.registry, "available", available)
    import threading

    stop = threading.Event()
    n = []

    def wait(_):
        n.append(1)
        if len(n) == 2:
            stop.set()

    scheduler.run_loop(0, wait=wait, now=lambda: NOW, stop=stop)
    assert built == [1] and closed == [1]


@pytest.fixture
def cats_db(db):
    sync_categories(settings.CATEGORIES_DIR)
    Category.objects.update(active=False)


def test_tick_without_connectors_closes_them(cats_db, monkeypatch):
    closed = []

    class C(FakeConnector):
        def close(self):
            closed.append(1)

    monkeypatch.setattr(scheduler.registry, "available", lambda *a, **k: {"fake": C({})})
    scheduler.tick(NOW)
    assert closed == [1]


# 8
def test_finished_at_is_actual_end_time(milk, monkeypatch):
    end = NOW + timedelta(minutes=7)
    monkeypatch.setattr("catalog.ingest.timezone.now", lambda: end)
    run = ingest(FakeConnector({}), milk, NOW, off_lookup=no_off)
    assert run.started_at == NOW and run.finished_at == end
    run = ingest(FakeConnector({}, error=RuntimeError("x")), milk, NOW, off_lookup=no_off)
    assert run.finished_at == end


# 10
@pytest.mark.parametrize(
    "refresh,cutoff",
    [(0, 24), (12, 0), (-1, 24), (24, 24), (30, 24)],
)
def test_settings_check_rejects_bad_intervals(settings, refresh, cutoff):
    settings.REFRESH_INTERVAL = timedelta(hours=refresh)
    settings.FRESHNESS_CUTOFF = timedelta(hours=cutoff)
    with pytest.raises(SystemCheckError):
        call_command("check")


def test_settings_check_accepts_defaults(settings):
    settings.REFRESH_INTERVAL = timedelta(hours=1)
    settings.FRESHNESS_CUTOFF = timedelta(hours=2)
    call_command("check")


# 11
def test_merge_once_per_product_per_run(milk, monkeypatch):
    import catalog.ingest as mod

    calls = []
    real = mod.merge_product
    monkeypatch.setattr(mod, "merge_product", lambda p, now=None: (calls.append(p.pk), real(p, now))[1])
    offers = [raw(item="A1", upc="036000291452"), raw(item="B1", upc="036000291452")]
    conn = FakeConnector({milk.search[0]: SearchResult(offers, 0)})
    ingest(conn, milk, NOW, off_lookup=no_off)
    assert len(calls) == 1 and Product.objects.count() == 1
