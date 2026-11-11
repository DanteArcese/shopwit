from django.contrib import admin, messages
from django.db import models, transaction
from django.utils import timezone

from collections import Counter

from .demand import curate_next
from .forms import ProductAdminForm
from .merge import merge_product
from .models import (
    Category,
    CurateNext,
    FetchJob,
    IngestionRun,
    Offer,
    OpenPriceObservation,
    Product,
    ReviewItem,
    SourceBudget,
    Store,
)
from .outliers import approve, parse_detail


class DetailPrefixFilter(admin.SimpleListFilter):
    title = "detail prefix"
    parameter_name = "detail_prefix"
    prefixes = ("attribute:", "quantity", "unit_price:", "category", "outlier:")

    def lookups(self, request, model_admin):
        return [(p, p) for p in self.prefixes]

    def queryset(self, request, queryset):
        return queryset.filter(detail__startswith=self.value()) if self.value() else queryset


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewItem)
class ReviewItemAdmin(admin.ModelAdmin):
    readonly_fields = ("product", "detail", "reason", "status", "resolved_at", "outlier")
    fields = readonly_fields

    list_display = ("product", "category", "detail", "outlier", "reason", "status", "created_at")
    list_filter = ("status", "reason", DetailPrefixFilter, "product__category")
    search_fields = ("product__title",)
    list_select_related = ("product__category",)
    actions = ["mark_resolved", "approve_real_price"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="category", ordering="product__category__slug")
    def category(self, obj):
        return obj.product.category

    @admin.display(description="unit price vs median")
    def outlier(self, obj) -> str:
        """For an outlier item: the held offer's unit price and the median it was judged by."""
        parsed = parse_detail(obj.detail) if obj.reason == ReviewItem.Reason.OUTLIER else None
        if parsed is None:
            return "-"
        comparison, offer_id = parsed
        offer = Offer.objects.filter(pk=offer_id).only("unit_prices", "held_comparisons").first()
        if offer is None:
            return "offer gone"
        price = offer.unit_prices.get(comparison, "?")
        median = offer.held_comparisons.get(comparison)
        return f"{price} vs median {median}" if median else f"{price} (no longer held)"

    @admin.action(description="Approve as real price (outlier items: never held again)")
    def approve_real_price(self, request, queryset):
        now = timezone.now()
        n = 0
        items = queryset.filter(reason=ReviewItem.Reason.OUTLIER, status=ReviewItem.Status.OPEN)
        for item in items:
            parsed = parse_detail(item.detail)
            offer = Offer.objects.filter(pk=parsed[1]).first() if parsed else None
            if offer is not None:
                approve(offer, parsed[0])
            item.status = ReviewItem.Status.RESOLVED
            item.resolved_at = now
            item.save(update_fields=["status", "resolved_at", "updated_at"])
            n += 1
        self.message_user(request, f"{n} outlier price(s) approved.", messages.SUCCESS)

    @admin.action(description="Mark resolved (reopens on the next ingest if the condition persists)")
    def mark_resolved(self, request, queryset):
        now = timezone.now()
        n = 0
        for item in queryset.filter(status=ReviewItem.Status.OPEN):
            item.status = ReviewItem.Status.RESOLVED
            item.resolved_at = now
            item.save(update_fields=["status", "resolved_at", "updated_at"])
            n += 1
        self.message_user(request, f"{n} review item(s) resolved.", messages.SUCCESS)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = ("title", "brand", "category")
    list_filter = ("category",)
    search_fields = ("title", "identity", "upc")
    readonly_fields = ("identity", "upc", "category", "title", "brand", "attributes", "measures")
    fields = readonly_fields + ("manual_attributes",)

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        # Only manual_attributes is written, then merged under the same row lock.
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=obj.pk)
            obj.save(update_fields=["manual_attributes", "updated_at"])
            merge_product(obj)


@admin.register(Offer)
class OfferAdmin(ReadOnlyAdmin):
    """Read-only: Walmart's terms forbid editing feed content, and edits bypass merge."""

    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}

    list_display = ("title", "retailer", "price", "last_seen_at")
    list_filter = ("retailer", "category")
    search_fields = ("title", "retailer_item_id")


@admin.register(IngestionRun)
class IngestionRunAdmin(ReadOnlyAdmin):
    list_display = ("connector", "category_slug", "status", "started_at", "fetched", "matched", "unresolved")
    list_filter = ("connector", "status")


@admin.register(Category)
class CategoryAdmin(ReadOnlyAdmin):
    list_display = ("slug", "name", "dimension", "active")
    list_filter = ("active",)


@admin.register(Store)
class StoreAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "city", "chain_name", "country_code")
    list_filter = ("chain_name",)
    search_fields = ("code", "name", "chain_name", "city")


@admin.register(OpenPriceObservation)
class OpenPriceObservationAdmin(ReadOnlyAdmin):
    list_display = ("price_id", "product_name", "store", "price", "observed_on")
    list_select_related = ("store",)
    search_fields = ("product_name", "upc")


@admin.register(SourceBudget)
class SourceBudgetAdmin(admin.ModelAdmin):
    list_display = (
        "source",
        "capacity",
        "refill_per_second",
        "tokens",
        "refilled_at",
        "daily_limit",
        "daily_used",
        "day",
        "blocked_until",
        "consecutive_failures",
    )


@admin.register(FetchJob)
class FetchJobAdmin(ReadOnlyAdmin):
    # The phrase is deliberately neither listed nor searchable.
    list_display = ("id", "source", "origin", "status", "created_at", "finished_at", "result_count", "error")
    list_filter = ("source", "origin", "status")
    exclude = ("phrase",)


def _now():
    return timezone.now()


@admin.register(CurateNext)
class CurateNextAdmin(ReadOnlyAdmin):
    """Phrases searched on 3 or more distinct days that no category matched: curate these next.

    The raw SearchDemand model is deliberately not registered, so rarer phrases never show.
    """

    list_display = ("phrase", "days_seen", "count", "last_seen", "held_results", "top_store_path")
    list_per_page = 50
    ordering = ()

    def get_queryset(self, request):
        return curate_next()

    def get_ordering(self, request):
        return ()

    def _held(self, obj) -> tuple[int, str]:
        cached = getattr(obj, "_held", None)
        if cached is None:
            from .generic import match_offers

            offers = match_offers(obj.phrase, _now()).offers
            paths = Counter(o.category_path for o in offers if o.category_path)
            top = min(paths, key=lambda p: (-paths[p], p)) if paths else ""
            cached = obj._held = (len({o.product_id for o in offers}), top)
        return cached

    @admin.display(description="held results")
    def held_results(self, obj) -> int:
        return self._held(obj)[0]

    @admin.display(description="top store path")
    def top_store_path(self, obj) -> str:
        return self._held(obj)[1] or "-"
