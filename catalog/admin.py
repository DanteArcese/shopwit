from django.contrib import admin, messages
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
    list_display = ("product", "category", "detail", "status", "created_at")
    list_filter = ("status", DetailPrefixFilter, "product__category")
    search_fields = ("product__title",)
    list_select_related = ("product__category",)
    actions = ["mark_resolved"]

    @admin.display(description="category", ordering="product__category__slug")
    def category(self, obj):
        return obj.product.category

    @admin.action(description="Mark resolved")
    def mark_resolved(self, request, queryset):
        n = queryset.filter(status=ReviewItem.Status.OPEN).update(
            status=ReviewItem.Status.RESOLVED, resolved_at=timezone.now()
        )
        self.message_user(request, f"{n} review item(s) resolved.", messages.SUCCESS)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = ("title", "brand", "category")
    list_filter = ("category",)
    search_fields = ("title", "identity", "upc")
    readonly_fields = ("attributes", "measures")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        merge_product(obj)


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
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
