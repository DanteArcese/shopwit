"""One list-storage interface over two backends: session JSON and account rows."""

from __future__ import annotations

import logging
from typing import Protocol

from django.db import transaction

from . import spec as sp
from .models import ListItem, ShoppingList
from .spec import ItemSpec, ListLimitError, ListSpec

# Counts only: never list names, items or ids.
logger = logging.getLogger("lists.store")


class ListStore(Protocol):
    def lists(self) -> list[ListSpec]: ...
    def get(self, list_id: str) -> ListSpec | None: ...
    def save(self, spec: ListSpec) -> None: ...
    def delete(self, list_id: str) -> None: ...


def _recent_first(specs: list[ListSpec]) -> list[ListSpec]:
    return sorted(specs, key=lambda s: s.updated_at, reverse=True)


class SessionListStore:
    KEY = "lists"

    def __init__(self, session):
        self.session = session

    def _all(self) -> list[ListSpec]:
        raw = self.session.get(self.KEY, [])
        if not isinstance(raw, list):
            logger.warning("dropped malformed session lists")
            return []
        specs = [s for s in map(sp.from_json, raw) if s is not None]
        if len(specs) != len(raw):
            logger.warning("dropped %d malformed session list(s)", len(raw) - len(specs))
        return specs

    def lists(self) -> list[ListSpec]:
        return _recent_first(self._all())

    def get(self, list_id: str) -> ListSpec | None:
        return next((s for s in self._all() if s.id == list_id), None)

    def save(self, spec: ListSpec) -> None:
        sp.check_items(spec)
        current = self._all()
        if all(s.id != spec.id for s in current) and len(current) >= sp.MAX_LISTS:
            raise ListLimitError(f"at most {sp.MAX_LISTS} lists")
        kept = [s for s in current if s.id != spec.id]
        self.session[self.KEY] = [sp.to_json(s) for s in (*kept, spec)]

    def delete(self, list_id: str) -> None:
        current = self._all()
        if any(s.id == list_id for s in current):
            self.session[self.KEY] = [sp.to_json(s) for s in current if s.id != list_id]


def _to_spec(row: ShoppingList) -> ListSpec:
    items = tuple(
        ItemSpec(
            id=i.uid,
            category=i.category_slug,
            filters=dict(i.filters),
            target_measure=i.target_measure,
            target_amount=i.target_amount,
            pinned_product=i.pinned_product_id,
            kind=i.kind,
            query=i.query,
            facets={k: tuple(v) for k, v in i.facets.items()},
            unit_system=i.unit_system,
        )
        for i in row.items.all()
    )
    return ListSpec(id=row.uid, name=row.name, items=items, updated_at=row.updated_at)


class AccountListStore:
    def __init__(self, user):
        self.user = user

    def _rows(self):
        return ShoppingList.objects.filter(user=self.user).prefetch_related("items")

    def lists(self) -> list[ListSpec]:
        return _recent_first([_to_spec(r) for r in self._rows()])

    def get(self, list_id: str) -> ListSpec | None:
        row = self._rows().filter(uid=list_id).first()
        return _to_spec(row) if row else None

    def save(self, spec: ListSpec) -> None:
        sp.check_items(spec)
        with transaction.atomic():
            row = ShoppingList.objects.filter(user=self.user, uid=spec.id).first()
            if row is None:
                if ShoppingList.objects.filter(user=self.user).count() >= sp.MAX_LISTS:
                    raise ListLimitError(f"at most {sp.MAX_LISTS} lists")
                row = ShoppingList.objects.create(user=self.user, uid=spec.id, name=spec.name)
            else:
                row.name = spec.name
                row.save()
            # update() bypasses auto_now so the spec's own timestamp round-trips.
            ShoppingList.objects.filter(pk=row.pk).update(updated_at=spec.updated_at)
            keep = [i.id for i in spec.items]
            row.items.exclude(uid__in=keep).delete()
            existing = {i.uid: i for i in row.items.all()}
            for position, item in enumerate(spec.items):
                fields = {
                    "position": position,
                    "kind": item.kind,
                    "category_slug": item.category,
                    "filters": dict(item.filters),
                    "query": item.query,
                    "facets": {k: list(v) for k, v in item.facets.items()},
                    "unit_system": item.unit_system,
                    "target_measure": item.target_measure,
                    "target_amount": item.target_amount,
                    "pinned_product_id": item.pinned_product,
                }
                obj = existing.get(item.id)
                if obj is None:
                    ListItem.objects.create(shopping_list=row, uid=item.id, **fields)
                else:
                    for k, v in fields.items():
                        setattr(obj, k, v)
                    obj.save()

    def delete(self, list_id: str) -> None:
        ShoppingList.objects.filter(user=self.user, uid=list_id).delete()


def store_for(request) -> ListStore:
    if request.user.is_authenticated:
        return AccountListStore(request.user)
    return SessionListStore(request.session)
