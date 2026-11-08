from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Category(TimestampedModel):
    slug = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    parent_slug = models.CharField(max_length=100, null=True, blank=True)
    dimension = models.CharField(max_length=20)
    comparison_default = models.CharField(max_length=40)
    comparison_alternates = models.JSONField(default=list)
    search = models.JSONField(default=list)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.slug


class AttributeDef(TimestampedModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="attribute_defs")
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=20)
    values = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["category", "name"], name="uniq_attributedef_category_name")
        ]


class Product(TimestampedModel):
    identity = models.CharField(max_length=255, unique=True)
    upc = models.CharField(max_length=32, null=True, blank=True, db_index=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=500)
    brand = models.CharField(max_length=200, null=True, blank=True)
    attributes = models.JSONField(default=dict)
    measures = models.JSONField(default=dict)
    manual_attributes = models.JSONField(default=dict)

    class Meta:
        indexes = [GinIndex(fields=["attributes"], opclasses=["jsonb_path_ops"], name="product_attributes_gin")]

    def __str__(self):
        return self.title


class Store(TimestampedModel):
    """An Open Prices store location (one OSM node/way/relation)."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    chain_code = models.CharField(max_length=50, db_index=True)
    chain_name = models.CharField(max_length=200)
    city = models.CharField(max_length=200, null=True, blank=True)
    postcode = models.CharField(max_length=20, null=True, blank=True)
    country_code = models.CharField(max_length=2)
    osm_type = models.CharField(max_length=10)
    osm_id = models.BigIntegerField()
    # WGS84 position from OpenStreetMap; null until a sync records it.
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"], name="store_lat_lon")]

    def __str__(self):
        return self.display

    @property
    def display(self) -> str:
        return f"{self.name}, {self.city}" if self.city else self.name


class Offer(TimestampedModel):
    class Kind(models.TextChoices):
        LIVE = "live"
        OBSERVED = "observed"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="offers")
    retailer = models.CharField(max_length=50)
    retailer_item_id = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    unit_prices = models.JSONField(default=dict)
    url = models.URLField(max_length=2000)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=500, default="")
    brand = models.CharField(max_length=200, null=True, blank=True)
    attributes = models.JSONField(default=dict)
    measures = models.JSONField(default=dict)
    unresolved = models.JSONField(default=list)
    image_url = models.URLField(max_length=2000, null=True, blank=True)
    last_seen_at = models.DateTimeField(db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LIVE, db_index=True)
    observed_on = models.DateField(null=True, blank=True)
    is_sale = models.BooleanField(default=False)
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["retailer", "retailer_item_id"], name="uniq_offer_retailer_item")
        ]


class ClickEvent(models.Model):
    """An outbound click. Deliberately stores no personal data."""

    offer = models.ForeignKey(Offer, null=True, on_delete=models.SET_NULL)
    retailer = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)


class PriceHistory(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="price_history")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class ReviewItem(TimestampedModel):
    class Status(models.TextChoices):
        OPEN = "open"
        RESOLVED = "resolved"

    class Reason(models.TextChoices):
        INGEST = "ingest"  # opened and resolved by the merge as data changes
        USER_REPORT = "user_report"  # a visitor's approved "wrong price or size" report

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="review_items")
    detail = models.CharField(max_length=255)
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.INGEST)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "detail"],
                condition=Q(status="open"),
                name="uniq_open_reviewitem_product_detail",
            )
        ]


class IngestionRun(TimestampedModel):
    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    connector = models.CharField(max_length=50)
    category_slug = models.CharField(max_length=100)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    fetched = models.IntegerField(default=0)
    matched = models.IntegerField(default=0)
    fully_parsed = models.IntegerField(default=0)
    unresolved = models.IntegerField(default=0)
    skipped = models.IntegerField(default=0)
    error = models.TextField(blank=True)


class OffEntry(models.Model):
    """Slim Open Food Facts / Open Products Facts cache row, keyed by normalized UPC."""

    upc = models.CharField(max_length=14, primary_key=True)
    source = models.CharField(max_length=3)
    labels = models.JSONField(default=list)
    quantity = models.CharField(max_length=200, null=True, blank=True)
    last_modified = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)


class OpenPriceObservation(models.Model):
    """One dated shelf price from Open Prices (ODbL data; kept apart from Offer)."""

    price_id = models.BigIntegerField(primary_key=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="observations")
    upc = models.CharField(max_length=14)
    product_name = models.CharField(max_length=500)
    brands = models.CharField(max_length=200, null=True, blank=True)
    quantity = models.CharField(max_length=200, null=True, blank=True)
    labels = models.JSONField(default=list)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    is_sale = models.BooleanField(default=False)
    observed_on = models.DateField(db_index=True)
    proof_type = models.CharField(max_length=20, null=True, blank=True)
    source = models.CharField(max_length=200, null=True, blank=True)
    fetched_at = models.DateTimeField()
