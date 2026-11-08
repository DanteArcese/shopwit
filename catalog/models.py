from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import F, Func, Q, Value


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
    # Generic (category-free) mode: one measure per offer, see normalize/generic.py.
    category_path = models.CharField(max_length=500, null=True, blank=True)
    labels = models.JSONField(default=list)
    measure_dim = models.CharField(max_length=10, null=True, blank=True)
    measure_amount = models.FloatField(null=True, blank=True)
    measure_reason = models.CharField(max_length=100, null=True, blank=True)
    generic_unit_price = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    search_vector = models.GeneratedField(
        expression=(
            SearchVector("title", weight="A", config="english")
            + SearchVector("brand", weight="B", config="english")
            # "/" must become a space: the parser reads "Food/Dairy" as one file-path token.
            + SearchVector(
                Func(F("category_path"), Value("/"), Value(" "), function="replace"),
                weight="C",
                config="english",
            )
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["retailer", "retailer_item_id"], name="uniq_offer_retailer_item")
        ]
        indexes = [
            GinIndex(fields=["search_vector"], name="offer_search_gin"),
            GinIndex(fields=["title"], opclasses=["gin_trgm_ops"], name="offer_title_trgm"),
            models.Index(fields=["measure_dim", "generic_unit_price"], name="offer_measure_price"),
            models.Index(fields=["kind", "currency", "last_seen_at"], name="offer_fresh"),
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
    quantity_unit = models.CharField(max_length=10, null=True, blank=True)
    categories = models.JSONField(default=list)
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
    categories = models.JSONField(default=list)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    is_sale = models.BooleanField(default=False)
    observed_on = models.DateField(db_index=True)
    proof_type = models.CharField(max_length=20, null=True, blank=True)
    source = models.CharField(max_length=200, null=True, blank=True)
    fetched_at = models.DateTimeField()


class FetchJob(models.Model):
    """A background live-fetch request for one phrase at one source."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        EXPIRED = "expired"
        SKIPPED = "skipped"

    ACTIVE = ("queued", "running")
    TERMINAL = ("succeeded", "failed", "expired", "skipped")

    source = models.CharField(max_length=30)
    phrase = models.CharField(max_length=100)  # normalized phrase only
    origin = models.CharField(max_length=10, default="search")  # search, list or warm
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    not_before = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)  # bumped per budgeted request
    finished_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    result_count = models.IntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)  # exception type name only

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "phrase"],
                condition=Q(status__in=["queued", "running"]),
                name="uniq_active_fetchjob",
            )
        ]
        indexes = [
            models.Index(
                fields=["not_before", "created_at"],
                condition=Q(status="queued"),
                name="fetchjob_due",
            )
        ]


class SourceBudget(models.Model):
    """A DB-backed token bucket plus daily cap for one budgeted host."""

    source = models.CharField(max_length=30, primary_key=True)
    capacity = models.FloatField()
    refill_per_second = models.FloatField()
    tokens = models.FloatField()
    refilled_at = models.DateTimeField()
    daily_limit = models.IntegerField(null=True, blank=True)
    daily_used = models.IntegerField(default=0)
    day = models.DateField()  # UTC day that daily_used counts
    blocked_until = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return self.source


class SearchDemand(models.Model):
    """Anonymous count of uncurated search phrases. Holds no user data."""

    phrase = models.CharField(max_length=100, unique=True)  # normalized phrase only
    count = models.PositiveIntegerField(default=0)
    days_seen = models.PositiveIntegerField(default=0)
    last_seen = models.DateField(db_index=True)


class CurateNext(SearchDemand):
    """Admin-only view of SearchDemand rows seen on enough days to be worth curating."""

    class Meta:
        proxy = True
        verbose_name = "curate-next phrase"
        verbose_name_plural = "curate next"
