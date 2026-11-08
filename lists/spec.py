"""List and item specs: plain frozen values, independent of where they are stored."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from catalog.units import TARGET_UNITS

from .fit import GENERIC_MEASURE_KEYS

# Logs carry counts only, never list names, queries, slugs or ids.
logger = logging.getLogger("lists.spec")

MAX_LISTS = 20
MAX_ITEMS = 100
MAX_NAME = 80
DEFAULT_NAME = "My list"
MAX_QUERY = 100
MAX_ID = 32  # ListItem.uid / ShoppingList.uid
MAX_CATEGORY = 100  # Category.slug
MAX_FILTERS = 20  # keys per filters / facets map
MAX_FILTER_VALUE = 100  # characters per key or value
MAX_FACET_VALUES = 20  # values per facet key
TARGET_QUANTUM = Decimal("0.001")
TARGET_MAX = Decimal("1000000")
UNIT_SYSTEMS = ("metric", "imperial")
CURATED_MEASURES = frozenset(key for units in TARGET_UNITS.values() for _l, key, _f in units.values())
GENERIC_MEASURES = GENERIC_MEASURE_KEYS


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


def check_items(spec: ListSpec) -> None:
    """Raise ListLimitError past MAX_ITEMS, ValueError on a repeated item id."""
    if len(spec.items) > MAX_ITEMS:
        raise ListLimitError(f"a list holds at most {MAX_ITEMS} items")
    if len({i.id for i in spec.items}) != len(spec.items):
        raise ValueError("item ids must be unique within a list")


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


def _short(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_FILTER_VALUE


def _str_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or len(value) > MAX_FILTERS:
        return None
    if not all(_short(k) and _short(v) for k, v in value.items()):
        return None
    return dict(value)


def _facet_map(value: object) -> dict[str, tuple[str, ...]] | None:
    if not isinstance(value, dict) or len(value) > MAX_FILTERS:
        return None
    out = {}
    for k, v in value.items():
        if not _short(k) or not isinstance(v, list | tuple) or len(v) > MAX_FACET_VALUES:
            return None
        if not all(_short(x) for x in v):
            return None
        out[k] = tuple(v)
    return out


def _target_amount(raw: object) -> Decimal | None:
    try:
        amount = Decimal(str(raw))
        if not amount.is_finite():
            return None
        amount = amount.quantize(TARGET_QUANTUM, rounding=ROUND_HALF_UP)
    except ArithmeticError:  # includes InvalidOperation from an oversized quantize
        return None
    return amount if 0 < amount <= TARGET_MAX else None


def item_from_json(data: object) -> ItemSpec | None:
    if not isinstance(data, dict):
        return None
    try:
        item_id = data["id"]
        measure = data["target_measure"]
        amount = _target_amount(data["target_amount"])
        kind = data.get("kind", "curated")  # legacy data has no kind
        category = data.get("category")
        query = data.get("query", "")
        pinned = data.get("pinned_product")
        unit_system = data.get("unit_system", "metric")  # legacy data has no unit_system
        filters = _str_map(data.get("filters", {}))
        facets = _facet_map(data.get("facets", {}))
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return None
    if not isinstance(item_id, str) or not 0 < len(item_id) <= MAX_ID:
        return None
    if not isinstance(measure, str) or amount is None:
        return None
    if filters is None or facets is None or not isinstance(query, str):
        return None
    if len(query) > MAX_QUERY:
        return None
    if pinned is not None and (not isinstance(pinned, int) or isinstance(pinned, bool)):
        return None
    if unit_system not in UNIT_SYSTEMS:
        return None
    if kind == "curated":
        if not isinstance(category, str) or not 0 < len(category) <= MAX_CATEGORY:
            return None
        if measure not in CURATED_MEASURES:
            return None
    elif kind == "generic":
        if not query or measure not in GENERIC_MEASURES:
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
    if not isinstance(list_id, str) or not 0 < len(list_id) <= MAX_ID or not isinstance(name, str):
        return None
    if not isinstance(raw_items, list):
        return None
    try:
        updated_at = datetime.fromisoformat(data["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if updated_at.tzinfo is None:
        return None
    items: list[ItemSpec] = []
    seen: set[str] = set()
    for item in map(item_from_json, raw_items):
        if item is not None and item.id not in seen and len(items) < MAX_ITEMS:
            seen.add(item.id)
            items.append(item)
    dropped = len(raw_items) - len(items)
    if dropped:
        logger.warning("dropped %d malformed, repeated or excess list item(s)", dropped)
    return ListSpec(id=list_id, name=clean_name(name), items=tuple(items), updated_at=updated_at)
