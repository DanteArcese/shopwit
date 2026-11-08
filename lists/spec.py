"""List and item specs: plain frozen values, independent of where they are stored."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

MAX_LISTS = 20
MAX_ITEMS = 100
MAX_NAME = 80
DEFAULT_NAME = "My list"
MAX_QUERY = 100
UNIT_SYSTEMS = ("metric", "imperial")


class ListLimitError(Exception):
    pass


@dataclass(frozen=True)
class ItemSpec:
    """One wanted thing.

    Curated: ``category`` slug + ``filters`` (canonical params from ``spec_params``) and
    ``target_measure`` a canonical measure. Generic (open search): ``query`` (normalized
    phrase) + ``facets``; ``target_measure`` is the dimension name, ``unit_system`` the
    system the size facets were entered in.
    """

    id: str
    category: str | None
    filters: Mapping[str, str]
    target_measure: str
    target_amount: Decimal
    pinned_product: int | None = None
    kind: Literal["curated", "generic"] = "curated"
    query: str = ""
    facets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unit_system: Literal["metric", "imperial"] = "metric"


@dataclass(frozen=True)
class ListSpec:
    id: str
    name: str
    items: tuple[ItemSpec, ...]
    updated_at: datetime


def new_id() -> str:
    return uuid.uuid4().hex


def clean_name(name: str | None) -> str:
    return " ".join((name or "").split())[:MAX_NAME].strip() or DEFAULT_NAME


def new_list(name: str, now: datetime) -> ListSpec:
    return ListSpec(id=new_id(), name=clean_name(name), items=(), updated_at=now)


def with_item(spec: ListSpec, item: ItemSpec, now: datetime) -> ListSpec:
    if len(spec.items) >= MAX_ITEMS:
        raise ListLimitError(f"a list holds at most {MAX_ITEMS} items")
    return replace(spec, items=(*spec.items, item), updated_at=now)


def without_item(spec: ListSpec, item_id: str, now: datetime) -> ListSpec:
    return replace(spec, items=tuple(i for i in spec.items if i.id != item_id), updated_at=now)


def replace_item(spec: ListSpec, item: ItemSpec, now: datetime) -> ListSpec:
    items = tuple(item if i.id == item.id else i for i in spec.items)
    return replace(spec, items=items, updated_at=now)


def renamed(spec: ListSpec, name: str, now: datetime) -> ListSpec:
    return replace(spec, name=clean_name(name), updated_at=now)


def item_to_json(item: ItemSpec) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "category": item.category,
        "filters": dict(item.filters),
        "target_measure": item.target_measure,
        "target_amount": str(item.target_amount),
        "pinned_product": item.pinned_product,
        "query": item.query,
        "facets": {k: list(v) for k, v in item.facets.items()},
        "unit_system": item.unit_system,
    }


def to_json(spec: ListSpec) -> dict:
    return {
        "id": spec.id,
        "name": spec.name,
        "items": [item_to_json(i) for i in spec.items],
        "updated_at": spec.updated_at.isoformat(),
    }


def _str_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return None
    return dict(value)


def _facet_map(value: object) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(value, dict):
        return None
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, list | tuple):
            return None
        if not all(isinstance(x, str) for x in v):
            return None
        out[k] = tuple(v)
    return out


def item_from_json(data: object) -> ItemSpec | None:
    if not isinstance(data, dict):
        return None
    try:
        item_id = data["id"]
        measure = data["target_measure"]
        amount = Decimal(str(data["target_amount"]))
        kind = data.get("kind", "curated")  # legacy data has no kind
        category = data.get("category")
        query = data.get("query", "")
        pinned = data.get("pinned_product")
        unit_system = data.get("unit_system", "metric")  # legacy data has no unit_system
        filters = _str_map(data.get("filters", {}))
        facets = _facet_map(data.get("facets", {}))
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return None
    if not isinstance(item_id, str) or not item_id or not isinstance(measure, str) or not measure:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    if filters is None or facets is None or not isinstance(query, str):
        return None
    if pinned is not None and (not isinstance(pinned, int) or isinstance(pinned, bool)):
        return None
    if unit_system not in UNIT_SYSTEMS:
        return None
    if kind == "curated":
        if not isinstance(category, str) or not category:
            return None
    elif kind == "generic":
        if not query:
            return None
        category = None
    else:
        return None
    return ItemSpec(
        id=item_id,
        category=category,
        filters=filters,
        target_measure=measure,
        target_amount=amount,
        pinned_product=pinned,
        kind=kind,
        query=query,
        facets=facets,
        unit_system=unit_system,
    )


def from_json(data: object) -> ListSpec | None:
    if not isinstance(data, dict):
        return None
    list_id, name, raw_items = data.get("id"), data.get("name"), data.get("items")
    if not isinstance(list_id, str) or not list_id or not isinstance(name, str):
        return None
    if not isinstance(raw_items, list):
        return None
    try:
        updated_at = datetime.fromisoformat(data["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if updated_at.tzinfo is None:
        return None
    items = tuple(i for i in map(item_from_json, raw_items) if i is not None)
    return ListSpec(
        id=list_id, name=clean_name(name), items=items[:MAX_ITEMS], updated_at=updated_at
    )
