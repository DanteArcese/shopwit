"""Validation and admin forms for manual attribute overrides."""

from __future__ import annotations

from collections.abc import Mapping

from django import forms
from django.core.exceptions import ValidationError

from .models import Category, Product

BRAND_TIERS = ("name_brand", "store_brand")


def validate_overrides(category: Category | None, data: Mapping[str, object]) -> dict[str, object]:
    """Check override values against the category's attribute definitions."""
    if not isinstance(data, Mapping):
        raise ValidationError("Overrides must be a JSON object of {name: value}.")
    if data and category is None:
        raise ValidationError("A category is required to set overrides.")
    defs = {a.name: a for a in category.attribute_defs.all()} if category else {}
    clean: dict[str, object] = {}
    for name, value in data.items():
        if name == "brand_tier":
            ok = value in BRAND_TIERS
        elif name not in defs:
            raise ValidationError(f"Unknown attribute: {name}")
        else:
            a = defs[name]
            if a.type == "bool":
                ok = isinstance(value, bool)
            elif a.type == "int":
                ok = isinstance(value, int) and not isinstance(value, bool)
            else:
                ok = isinstance(value, str)
            ok = ok and (not a.values or value in a.values)
        if not ok:
            raise ValidationError(f"Invalid value for {name}: {value!r}")
        clean[name] = value
    return clean


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["identity", "upc", "category", "title", "brand", "manual_attributes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manual_attributes"].required = False

    def clean(self):
        cleaned = super().clean()
        if "manual_attributes" in cleaned and "category" in cleaned:
            try:
                cleaned["manual_attributes"] = validate_overrides(
                    cleaned["category"], cleaned["manual_attributes"] or {}
                )
            except ValidationError as exc:
                self.add_error("manual_attributes", exc)
        return cleaned
