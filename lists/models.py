from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import TimestampedModel


class ShoppingList(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shopping_lists"
    )
    uid = models.CharField(max_length=32)
    name = models.CharField(max_length=80)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "uid"], name="uniq_shoppinglist_user_uid")
        ]

    def __str__(self):
        return self.name


class ListItem(TimestampedModel):
    shopping_list = models.ForeignKey(ShoppingList, on_delete=models.CASCADE, related_name="items")
    uid = models.CharField(max_length=32)
    position = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, default="curated")
    # Slug, not FK: categories are deactivated, never deleted; resolution reports inactive ones.
    category_slug = models.CharField(max_length=100, null=True, blank=True)
    filters = models.JSONField(default=dict)
    query = models.CharField(max_length=100, blank=True)
    facets = models.JSONField(default=dict)
    unit_system = models.CharField(max_length=10, default="metric")
    target_measure = models.CharField(max_length=20)
    target_amount = models.DecimalField(max_digits=14, decimal_places=3)
    pinned_product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["shopping_list", "uid"], name="uniq_listitem_list_uid"),
            models.CheckConstraint(
                condition=Q(kind="curated", category_slug__isnull=False)
                | (
                    Q(kind="generic")
                    & ~Q(query="")
                    & Q(unit_system__in=["metric", "imperial"])
                ),
                name="listitem_kind_fields",
            ),
        ]
