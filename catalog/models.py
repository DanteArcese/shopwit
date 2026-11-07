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


class Offer(TimestampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="offers")
    retailer = models.CharField(max_length=50)
    retailer_item_id = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    unit_prices = models.JSONField(default=dict)
    url = models.URLField(max_length=2000)
    image_url = models.URLField(max_length=2000, null=True, blank=True)
    last_seen_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["retailer", "retailer_item_id"], name="uniq_offer_retailer_item")
        ]


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

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="review_items")
    detail = models.CharField(max_length=255)
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
