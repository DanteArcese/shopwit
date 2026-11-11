"""Where the outlier pass (#127) runs, and the admin approval."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from catalog import scheduler
from catalog.categories_sync import sync_categories
from catalog.models import Category, Offer, Product, ReviewItem
from catalog.observed import sync_open_prices
from catalog.outliers import detail_for, flag_unit_price_outliers
from tests.catalog.helpers import NOW
from tests.catalog.test_observed import FakeClient, loc, no_off, obs


@pytest.fixture
def cats(db):
    sync_categories(settings.CATEGORIES_DIR)


def gallons(*quantities):
    """One observation per store, each for its own UPC, all whole milk."""
    return [
        obs(n, "4.99", location=loc(n), upc=f"081162002{n:04d}", quantity=q)
        for n, q in enumerate(quantities, start=1)
    ]


def test_open_prices_sync_runs_the_pass(cats):
    run = sync_open_prices(
        FakeClient(gallons("1 gal", "1 gal", "1 gal", "1 gal", "100 gal")),
        NOW,
        off_lookup=no_off,
    )
    assert run.status == "succeeded"
    offers = Offer.objects.filter(category__slug="dairy/milk")
    assert offers.count() == 5
    held = offers.exclude(held_comparisons={})
    assert [o.measures for o in held] == [
        offers.get(retailer_item_id__startswith="0811620020005").measures
    ]
    [item] = ReviewItem.objects.filter(reason="outlier", status="open")
    assert item.detail == detail_for("per_volume", held.get().pk)


def test_tick_runs_the_pass_after_a_successful_run(cats, monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler, "flag_outliers_safely", lambda now: calls.append(now)
    )
    monkeypatch.setattr(scheduler, "_outliers_checked_at", None)
    from tests.catalog.test_scheduler import FakeConnector

    scheduler.tick(NOW, {"fake": FakeConnector()})
    assert calls == [NOW]
    # nothing ran, no fetch result and under an hour: no pass
    scheduler.tick(NOW + timedelta(minutes=5), {"fake": FakeConnector()})
    assert calls == [NOW]
    # an hour on, the pass runs anyway (offers age out of freshness)
    scheduler.tick(NOW + timedelta(hours=1), {"fake": FakeConnector()})
    assert calls == [NOW, NOW + timedelta(hours=1)]


def test_tick_runs_the_pass_after_a_fetch_job_result(cats, monkeypatch):
    from catalog.models import FetchJob

    calls = []
    monkeypatch.setattr(
        scheduler, "flag_outliers_safely", lambda now: calls.append(now)
    )
    monkeypatch.setattr(scheduler, "_outliers_checked_at", NOW)
    FetchJob.objects.create(
        source="x",
        phrase="milk",
        status=FetchJob.Status.SUCCEEDED,
        not_before=NOW,
        finished_at=NOW + timedelta(minutes=1),
        result_count=3,
    )
    scheduler.tick(NOW + timedelta(minutes=5), {})
    assert calls == [NOW + timedelta(minutes=5)]


# --- admin approval --------------------------------------------------------------


@pytest.fixture
def admin_client(client, cats):
    user = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
    client.force_login(user)
    return client


def held_offer():
    rice = Category.objects.get(slug="pantry/rice")
    offers = []
    for n, up in enumerate(["0.01", "0.01", "0.01", "0.01", "0.0001"], start=1):
        product = Product.objects.create(
            identity=f"r{n}", title=f"Rice {n}", category=rice
        )
        offers.append(
            Offer.objects.create(
                product=product,
                retailer="walmart",
                retailer_item_id=f"r{n}",
                price=Decimal("3.00"),
                currency="USD",
                url="https://example.com/r",
                category=rice,
                title=f"Rice {n}",
                unit_prices={"per_mass": up},
                last_seen_at=NOW,
                kind="live",
            )
        )
    flag_unit_price_outliers(NOW)
    return offers[-1]


def test_admin_lists_outlier_with_numbers(admin_client):
    held = held_offer()
    html = admin_client.get(
        "/admin/catalog/reviewitem/?reason=outlier"
    ).content.decode()
    assert detail_for("per_mass", held.pk) in html
    assert "0.0001 vs median 0.01" in html


def test_admin_approve_as_real_price_survives_reruns(admin_client):
    held = held_offer()
    item = ReviewItem.objects.get(reason="outlier")
    r = admin_client.post(
        "/admin/catalog/reviewitem/",
        {"action": "approve_real_price", "_selected_action": [item.pk]},
    )
    assert r.status_code == 302
    held.refresh_from_db()
    assert held.approved_comparisons == ["per_mass"] and held.held_comparisons == {}
    item.refresh_from_db()
    assert item.status == "resolved" and item.resolved_at is not None
    flag_unit_price_outliers(NOW)
    flag_unit_price_outliers(NOW)
    held.refresh_from_db()
    assert held.held_comparisons == {}
    assert not ReviewItem.objects.filter(reason="outlier", status="open").exists()


def test_admin_approve_ignores_other_items(admin_client):
    held_offer()
    other = ReviewItem.objects.create(
        product=Product.objects.first(), detail="attribute:ply"
    )
    admin_client.post(
        "/admin/catalog/reviewitem/",
        {"action": "approve_real_price", "_selected_action": [other.pk]},
    )
    other.refresh_from_db()
    assert other.status == "open"


def test_refresh_command_runs_the_pass(cats, monkeypatch, settings):
    from django.core.management import call_command

    from tests.catalog.test_scheduler import RECORDINGS

    settings.DEBUG = True
    calls = []
    monkeypatch.setattr(
        "catalog.management.commands.refresh.flag_outliers_safely", lambda now: calls.append(now)
    )
    call_command("refresh", category="dairy/milk", recordings=str(RECORDINGS))
    assert len(calls) == 1
