from django.contrib import admin, messages
from django.db import models, transaction
from django.utils import timezone

from .forms import ProductAdminForm
from .merge import merge_product
from .models import Category, IngestionRun, Offer, Product, ReviewItem


class DetailPrefixFilter(admin.SimpleListFilter):
    title = "detail prefix"
    parameter_name = "detail_prefix"
    prefixes = ("attribute:", "quantity", "unit_price:", "category")

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
    readonly_fields = ("product", "detail", "status", "resolved_at")
    fields = readonly_fields

    list_display = ("product", "category", "detail", "status", "created_at")
    list_filter = ("status", DetailPrefixFilter, "product__category")
    search_fields = ("product__title",)
    list_select_related = ("product__category",)
    actions = ["mark_resolved"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="category", ordering="product__category__slug")
    def category(self, obj):
        return obj.product.category

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
