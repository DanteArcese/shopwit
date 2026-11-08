from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.postgres.search import SearchQuery
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from catalog.models import FetchJob, Offer, Product, ReviewItem, Store


def make_product(identity="p1"):
    return Product.objects.create(identity=identity, title="Milk")


def make_offer(product, **kw):
    fields = dict(
        product=product,
        retailer="walmart",
        retailer_item_id="1",
        price=Decimal("3.99"),
        currency="USD",
        url="https://example.com/1",
        last_seen_at=timezone.now(),
    )
    fields.update(kw)
    return Offer.objects.create(**fields)


@pytest.mark.django_db
def test_offer_unique_per_retailer_item():
    product = make_product()
    make_offer(product)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_offer(product)
    make_offer(product, retailer="target")


@pytest.mark.django_db
def test_one_open_review_item_per_detail():
    product = make_product()
    first = ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")
    first.status = ReviewItem.Status.RESOLVED
    first.resolved_at = timezone.now()
    first.save()
    ReviewItem.objects.create(product=product, detail="attribute:fat:ambiguous")


def test_store_display():
    assert Store(name="Safeway", city="Mountain View").display == "Safeway, Mountain View"
    assert Store(name="Safeway", city=None).display == "Safeway"


@pytest.mark.django_db
def test_offer_defaults_live():
    offer = make_offer(make_product())
    assert (offer.kind, offer.observed_on, offer.is_sale, offer.store) == ("live", None, False, None)


@pytest.mark.django_db
def test_search_vector_generated():
    product = make_product()
    offer = make_offer(
        product, title="Oat Milk Barista", brand="Oatly", category_path="Food/Dairy Alternatives"
    )
    q = lambda w: Offer.objects.filter(search_vector=SearchQuery(w, config="english"))
    assert list(q("oatly")) == [offer]
    # The english stemmer reduces "Oatly" to "oat", so the title-only word is "barista".
    assert list(q("barista")) == [offer]
    assert list(q("dairy")) == [offer]
    offer.title = "Soy Drink"
    offer.save()
    offer.refresh_from_db()
    assert not q("barista").exists()
    assert q("soy").exists()
    assert q("oatly").exists()  # brand still indexed


@pytest.mark.django_db
def test_category_path_slashes_are_searchable():
    product = make_product()
    offer = make_offer(product, title="Whole", category_path="Open Food Facts/Dairies/Milks")
    assert list(Offer.objects.filter(search_vector=SearchQuery("milks", config="english"))) == [offer]


@pytest.mark.django_db
def test_trigram_extension_and_index():
    product = make_product()
    offer = make_offer(product, title="Charmin Toilet Paper")
    assert list(Offer.objects.filter(title__trigram_word_similar="tolet paper")) == [offer]
    with connection.cursor() as cur:
        cur.execute("select count(*) from pg_extension where extname = 'pg_trgm'")
        assert cur.fetchone()[0] == 1
        cur.execute("select indexname from pg_indexes where tablename = 'catalog_offer'")
        names = {r[0] for r in cur.fetchall()}
    assert {"offer_search_gin", "offer_title_trgm", "offer_measure_price", "offer_fresh"} <= names


@pytest.mark.django_db
def test_one_active_job_per_source_phrase():
    FetchJob.objects.create(source="walmart", phrase="oat milk", not_before=timezone.now())
    with pytest.raises(IntegrityError), transaction.atomic():
        FetchJob.objects.create(source="walmart", phrase="oat milk", not_before=timezone.now())
    FetchJob.objects.create(source="off-prices", phrase="oat milk", not_before=timezone.now())


@pytest.mark.django_db
def test_finished_job_does_not_block_a_new_one():
    now = timezone.now()
    FetchJob.objects.create(
        source="walmart", phrase="oat milk", status=FetchJob.Status.SUCCEEDED, not_before=now
    )
    FetchJob.objects.create(source="walmart", phrase="oat milk", not_before=now)
    assert FetchJob.ACTIVE == ("queued", "running")
    assert "succeeded" in FetchJob.TERMINAL


def test_settings_budget_defaults():
    b = settings.SOURCE_BUDGETS
    assert b["off-search"] == {"capacity": 10.0, "refill_per_second": pytest.approx(1 / 6), "daily_limit": None}
    assert b["openprices"] == {"capacity": 1.0, "refill_per_second": 1.0, "daily_limit": None}  # no burst
    assert b["walmart"]["daily_limit"] == 5000
    assert settings.FETCH_THREADS == 1
    assert settings.LIVE_OFF_ENABLED is False
