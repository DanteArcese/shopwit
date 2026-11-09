"""Sync category YAML definitions into the database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from normalize import load_categories

from .models import AttributeDef, Category


@dataclass(frozen=True)
class SyncResult:
    created: int
    updated: int
    deactivated: int


def _attr_rows(cat) -> dict[str, dict]:
    return {
        a.name: {"type": a.type, "values": list(a.values)} for a in cat.attributes.values()
    }


@transaction.atomic
def sync_categories(root: Path) -> SyncResult:
    """Upsert categories from ``root``; deactivate (never delete) those no longer present."""
    defs = load_categories(root)
    created = updated = 0

    for slug, cat in defs.items():
        fields = {
            "name": cat.name,
            "parent_slug": cat.parent,
            "dimension": cat.dimension,
            "comparison_default": cat.comparison_default,
            "comparison_alternates": list(cat.comparison_alternates),
            "search": list(cat.search),
            "food": cat.food,
            "active": True,
        }
        wanted = _attr_rows(cat)
        obj, was_created = Category.objects.get_or_create(slug=slug, defaults=fields)
        changed = False
        if was_created:
            created += 1
        else:
            dirty = [k for k, v in fields.items() if getattr(obj, k) != v]
            for k in dirty:
                setattr(obj, k, fields[k])
            if dirty:
                obj.save()
                changed = True

        existing = {a.name: a for a in obj.attribute_defs.all()}
        for name, a in existing.items():
            row = wanted.get(name)
            if row is None:
                a.delete()
                changed = True
            elif (a.type, a.values) != (row["type"], row["values"]):
                a.type, a.values = row["type"], row["values"]
                a.save()
                changed = True
        for name, row in wanted.items():
            if name not in existing:
                AttributeDef.objects.create(category=obj, name=name, **row)
                changed = True
        if changed and not was_created:
            updated += 1

    deactivated = Category.objects.filter(active=True).exclude(slug__in=defs).update(active=False)
    return SyncResult(created=created, updated=updated, deactivated=deactivated)
