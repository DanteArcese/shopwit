from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from catalog.categories_sync import sync_categories
from catalog.models import Category, IngestionRun, Offer, Product, ReviewItem

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


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
        product=p, retailer="a", retailer_item_id="1", price=Decimal("20.00"), currency="USD",
        url="https://example.com/1", category=tp, title="Soft TP", measures={"area_m2": 10.0},
        unresolved=["attribute:ply", "unit_price:area_x_ply"], last_seen_at=NOW,
    )
    ReviewItem.objects.create(product=p, detail="attribute:ply")
    IngestionRun.objects.create(connector="fake", category_slug="x", started_at=NOW)
    return p


def test_admin_pages_load(admin_client, product):
    for model in ("reviewitem", "product", "offer", "ingestionrun", "category"):
        assert admin_client.get(f"/admin/catalog/{model}/").status_code == 200
    r = admin_client.get("/admin/catalog/reviewitem/?status=open&detail_prefix=attribute:&q=Soft")
    assert r.status_code == 200
    assert b"Soft TP" in r.content
    assert admin_client.get(f"/admin/catalog/product/{product.pk}/change/").status_code == 200


def test_mark_resolved_action(admin_client, product):
    item = ReviewItem.objects.get()
    r = admin_client.post(
        "/admin/catalog/reviewitem/",
        {"action": "mark_resolved", "_selected_action": [item.pk]},
    )
    assert r.status_code == 302
    item.refresh_from_db()
    assert item.status == "resolved" and item.resolved_at is not None


def post_product(client, product, manual):
    import json

    return client.post(
        f"/admin/catalog/product/{product.pk}/change/",
        {
            "identity": product.identity, "title": product.title, "category": product.category_id,
            "manual_attributes": json.dumps(manual),
        },
    )


def test_admin_save_reapplies_overrides(admin_client, product):
    r = post_product(admin_client, product, {"ply": 2})
    assert r.status_code == 302
    product.refresh_from_db()
    assert product.attributes["ply"] == {"value": 2, "source": "manual"}
    offer = product.offers.get()
    assert Decimal(offer.unit_prices["area_x_ply"]) == Decimal("1")
    assert ReviewItem.objects.get().status == "resolved"


def test_admin_save_rejects_invalid_override(admin_client, product):
    r = post_product(admin_client, product, {"ply": 9})
    assert r.status_code == 200
    assert b"Invalid value for ply" in r.content
    product.refresh_from_db()
    assert product.manual_attributes == {}


def test_non_staff_redirected_to_login(client, db):
    user = get_user_model().objects.create_user("joe", "j@example.com", "pw")
    client.force_login(user)
    r = client.get("/admin/catalog/reviewitem/")
    assert r.status_code == 302 and "/admin/login/" in r["Location"]


def test_only_manual_attributes_editable(admin_client, product):
    import json

    r = admin_client.post(
        f"/admin/catalog/product/{product.pk}/change/",
        {"title": "Hacked", "brand": "Evil", "manual_attributes": json.dumps({"ply": 3})},
    )
    assert r.status_code == 302
    product.refresh_from_db()
    assert product.title == "Soft TP" and product.brand is None
    assert product.manual_attributes == {"ply": 3}
