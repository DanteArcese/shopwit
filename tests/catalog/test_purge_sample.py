from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.categories_sync import sync_categories
from catalog.models import Offer, Product, ReviewItem
from catalog.sample import sample_item_ids
from lists.models import ListItem, ShoppingList
from tests.catalog.helpers import NOW

RECORDINGS = Path(__file__).parent.parent / "connectors" / "recordings" / "walmart"


def run(*args):
    out = StringIO()
    call_command("purge_sample_data", *args, stdout=out)
    return out.getvalue()


def make_offer(product=None, retailer="walmart", item="999999999", category=None, kind="live"):
    product = product or Product.objects.create(identity=f"id:{retailer}:{item}", title="Real thing")
    extra = {"observed_on": NOW.date()} if kind == "observed" else {}
    return Offer.objects.create(
        product=product, retailer=retailer, retailer_item_id=item, price=Decimal("1.00"),
        currency="USD", url="https://example.com/", title=product.title, category=category,
        kind=kind, last_seen_at=NOW - timedelta(hours=1), **extra,
    )


@pytest.fixture
def loaded_sample(db, settings, monkeypatch):
    settings.DEBUG = True
    for var in ("WALMART_CONSUMER_ID", "WALMART_PRIVATE_KEY", "WALMART_KEY_VERSION"):
        monkeypatch.delenv(var, raising=False)
    sync_categories(settings.CATEGORIES_DIR)
    call_command("refresh", connector="walmart", category="dairy/milk", recordings=str(RECORDINGS), stdout=StringIO())
    assert Offer.objects.filter(retailer="walmart").exists()


def test_sample_item_ids_reads_recordings():
    ids = sample_item_ids(RECORDINGS)
    assert {"20101", "20107"} <= ids and all(isinstance(i, str) for i in ids)


def test_sample_item_ids_empty_dir_errors(tmp_path):
    with pytest.raises(CommandError):
        sample_item_ids(tmp_path)


def test_refresh_recordings_refuses_without_debug(db, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG is off"):
        call_command("refresh", connector="walmart", recordings=str(RECORDINGS))
    assert not Offer.objects.exists()


def test_purge_dry_run_deletes_nothing(loaded_sample):
    before, products = Offer.objects.count(), Product.objects.count()
    out = run("--recordings", str(RECORDINGS))
    assert "dry run" in out and f"offers={before}" in out
    assert Offer.objects.count() == before and Product.objects.count() == products


def test_purge_default_dir_is_dry_run(loaded_sample):
    assert "dry run" in run()


def test_purge_apply_removes_sample(loaded_sample):
    out = run("--apply")
    assert "deleted" in out and "dry run" not in out
    assert not Offer.objects.exists() and not Product.objects.exists()


def test_purge_keeps_shared_products_and_real_offers(loaded_sample):
    sample = Offer.objects.filter(retailer="walmart").first()
    observed = make_offer(product=sample.product, retailer="op-safeway", item="u@n1", kind="observed")
    real = make_offer(item="999999999")
    run("--apply")
    assert not Offer.objects.filter(retailer="walmart").exclude(pk=real.pk).exists()
    assert Offer.objects.filter(pk__in=[observed.pk, real.pk]).count() == 2
    assert Product.objects.filter(pk=sample.product_id).exists()


def test_purge_covers_generic_offers(loaded_sample):
    loaded = set(Offer.objects.values_list("retailer_item_id", flat=True))
    unloaded = sorted(sample_item_ids(RECORDINGS) - loaded)[0]
    g = make_offer(item=unloaded, category=None)  # uncategorized sample offer
    Offer.objects.filter(pk=g.pk).update(url=f"https://www.walmart.com/ip/{unloaded}")
    run("--apply")
    assert not Offer.objects.filter(pk=g.pk).exists()


def test_purge_unpins_account_items_and_drops_review_items(loaded_sample, django_user_model):
    product = Offer.objects.filter(retailer="walmart").first().product
    ReviewItem.objects.create(product=product, detail="size looks wrong")
    user = django_user_model.objects.create_user("u@example.com")
    shopping_list = ShoppingList.objects.create(user=user, uid="b" * 32, name="L")
    item = ListItem.objects.create(
        shopping_list=shopping_list, uid="a" * 32, position=0, category_slug="dairy/milk",
        target_measure="volume_ml", target_amount=Decimal("1000"), pinned_product=product,
    )
    run("--apply")
    item.refresh_from_db()
    assert item.pinned_product is None  # SET_NULL: the item stays, unpinned
    assert not ReviewItem.objects.exists()


def test_purge_missing_dir_errors(db, tmp_path):
    with pytest.raises(CommandError):
        call_command("purge_sample_data", recordings=str(tmp_path / "nope"))


def test_purge_remerges_surviving_products(loaded_sample):
    sample = Offer.objects.filter(retailer="walmart").first()
    product = sample.product
    observed = make_offer(product=product, retailer="op-safeway", item="u@n2", kind="observed")
    Offer.objects.filter(pk=observed.pk).update(
        title="Real observed title",
        category=sample.category,
        measures=sample.measures,
        attributes={"fat": {"value": "2", "source": "title"}},
        unit_prices={"stale": "1"},
    )
    product.title = sample.title
    product.save()
    out = run("--apply")
    assert "products_remerged=" in out and "pins_cleared=" in out
    product.refresh_from_db()
    assert product.title == "Real observed title"
    observed.refresh_from_db()
    assert observed.unit_prices and "stale" not in observed.unit_prices
    assert "Great Value" not in (product.brand or "")


def test_purge_keeps_colliding_real_offer(loaded_sample):
    loaded = set(Offer.objects.values_list("retailer_item_id", flat=True))
    unloaded = sorted(sample_item_ids(RECORDINGS) - loaded)[0]
    real = make_offer(item=unloaded)
    Offer.objects.filter(pk=real.pk).update(title="Totally different cereal", url="https://www.walmart.com/ip/other-thing")
    run("--apply")
    assert Offer.objects.filter(pk=real.pk).exists()
