"""Display text for list items and resolutions. No state, no queries beyond what is passed in."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from catalog.browse import SIZE_KEYS
from catalog.generic import GENERIC_TARGET_UNITS, load_label_allowlist
from catalog.retailers import display_name
from catalog.units import (
    TARGET_UNITS,
    display_generic_size,
    display_size,
    display_target,
    format_money,
    target_units_for,
)

from .fit import Fit
from .resolve import ItemResolution, ListResolution
from .spec import GENERIC_MEASURES, ItemSpec

GENERIC_BY = {
    "volume": "volume",
    "mass": "weight",
    "oz_labeled": "oz as labeled",
    "count": "count",
}
_SIZE_FACET_NAMES = {
    "vol": "volume",
    "mass": "weight",
    "oz": "oz as labeled",
    "count": "count",
}
_TIERS = {"name_brand": "name brand", "store_brand": "store brand"}
_MEASURE_DIMENSION: dict[str, str] = {}
for _dim, _units in TARGET_UNITS.items():
    for _l, _key, _f in _units.values():
        _MEASURE_DIMENSION.setdefault(_key, _dim)


def dimension_of(measure: str, category_dimension: str | None = None) -> str:
    """The dimension of an item's target measure (curated key or generic dimension name).

    "sheets" is a target of both paper and tissue; a curated item's category dimension
    (when the caller has it) settles which."""
    if measure in GENERIC_MEASURES:
        return measure
    if category_dimension is not None and measure in {
        key for _l, key, _f in TARGET_UNITS.get(category_dimension, {}).values()
    }:
        return category_dimension
    return _MEASURE_DIMENSION[measure]


def _human(name: str) -> str:
    return name.replace("_", " ")


def _curated_filter_labels(
    item: ItemSpec, unit_system: str, category_dimension: str | None = None
) -> list[str]:
    labels = []
    dimension = dimension_of(item.target_measure, category_dimension)
    size_key = SIZE_KEYS.get(dimension)
    for key, value in sorted(item.filters.items()):
        if value == "any":
            continue
        if key == "brand_tier":
            labels.append(_TIERS.get(value, _human(value)))
        elif key in ("size_min", "size_max"):
            try:
                size = (
                    display_size({size_key: float(value)}, dimension, unit_system)
                    if size_key
                    else ""
                )
            except ValueError:
                size = ""
            labels.append(
                f"{'min' if key == 'size_min' else 'max'} size {size or value}"
            )
        elif value == "true":
            labels.append(_human(key))
        elif value == "false":
            labels.append(f"not {_human(key)}")
        else:
            labels.append(f"{_human(key)} {value}")
    return labels


def _generic_facet_labels(item: ItemSpec) -> list[str]:
    allowlist = load_label_allowlist(settings.LABELS_FILE)
    labels = []
    for key, values in sorted(item.facets.items()):
        if key == "brand":
            labels += [f"brand {v}" for v in values]
        elif key == "brand_tier":
            labels += [_TIERS.get(v, _human(v)) for v in values]
        elif key == "label":
            labels += [allowlist.get(v, v) for v in values]
        elif key == "path":
            labels += [v.strip("/").rsplit("/", 1)[-1] for v in values]
        else:
            name, _, end = key.rpartition("_")
            labels += [f"{_SIZE_FACET_NAMES.get(name, name)} {end} {v}" for v in values]
    return labels


def item_label(
    item: ItemSpec,
    category_name: str | None,
    unit_system: str,
    category_dimension: str | None = None,
) -> str:
    """ "Milk · fat 2 · organic" or "'oat milk' · brand x · by volume"."""
    if item.kind == "generic":
        parts = [
            f"'{item.query}'",
            *_generic_facet_labels(item),
            f"by {GENERIC_BY[item.target_measure]}",
        ]
    else:
        parts = [
            category_name or item.category or "",
            *_curated_filter_labels(item, unit_system, category_dimension),
        ]
    return " · ".join(p for p in parts if p)


def target_text(item: ItemSpec, unit_system: str, category_dimension: str | None = None) -> str:
    if item.kind == "generic":
        return display_generic_size(
            item.target_measure, float(item.target_amount), unit_system
        )
    return display_target(
        item.target_measure,
        item.target_amount,
        dimension_of(item.target_measure, category_dimension),
        unit_system,
    )


def target_text_for(
    item: ItemSpec, amount: Decimal, unit_system: str, category_dimension: str | None = None
) -> str:
    if item.kind == "generic":
        return display_generic_size(item.target_measure, float(amount), unit_system)
    return display_target(
        item.target_measure, amount, dimension_of(item.target_measure, category_dimension), unit_system
    )


def present_fit(
    item: ItemSpec, fit: Fit, unit_system: str, category_dimension: str | None = None
) -> dict:
    offer = fit.offer
    return {
        "packages": fit.packages,
        "size": target_text_for(item, fit.package_size, unit_system, category_dimension),
        "store": display_name(offer.retailer),
        "price": format_money(fit.total_price, offer.currency),
        "title": offer.title or "",
        "offer": offer,
        "product_id": offer.product_id,
    }


def present_item(res: ItemResolution, unit_system: str) -> dict:
    item = res.item
    cat_dim = res.category.dimension if res.category else None
    dimension = dimension_of(item.target_measure, cat_dim)
    units = []
    for code in target_units_for(dimension, unit_system):
        if item.kind == "generic":
            label = GENERIC_TARGET_UNITS[dimension][code][0]
        else:
            label = TARGET_UNITS[dimension][code][0]
        units.append((code, label))
    return {
        "id": item.id,
        "label": item_label(
            item, res.category.name if res.category else None, unit_system, cat_dim
        ),
        "target": target_text(item, unit_system, cat_dim),
        "reason": res.reason,
        "best": present_fit(item, res.best, unit_system, cat_dim) if res.best else None,
        "alternatives": [present_fit(item, f, unit_system, cat_dim) for f in res.alternatives],
        "pinned": item.pinned_product is not None,
        "units": units,
    }


def present_list(res: ListResolution, unit_system: str) -> dict:
    items = [present_item(r, unit_system) for r in res.items]
    names = {i["id"]: i["label"] for i in items}
    money = (
        (lambda amount: format_money(amount, res.currency)) if res.currency else None
    )
    totals = []
    if money:
        for t in res.totals:
            totals.append(
                {
                    "store": display_name(t.store),
                    "total": money(t.total),
                    "missing": len(t.missing),
                    "single": res.single is not None and t.store == res.single.store,
                }
            )
    split = None
    if money and res.split is not None:
        by_store: dict[str, list[str]] = {}
        for item_id, store in res.split.assignments.items():
            by_store.setdefault(store, []).append(names[item_id])
        split = {
            "total": money(res.split.total),
            "stores": [
                {"store": display_name(s), "items": by_store[s]}
                for s in sorted(by_store, key=display_name)
            ],
            "saves": (
                money(res.single.total - res.split.total)
                if res.single is not None and res.single.total > res.split.total
                else None
            ),
        }
    amazon = any(
        f["offer"].retailer == "amazon"
        for i in items
        for f in ([i["best"]] if i["best"] else []) + i["alternatives"]
    )
    return {
        "items": items,
        "totals": totals,
        "single": (
            {"store": display_name(res.single.store), "total": money(res.single.total)}
            if money and res.single is not None
            else None
        ),
        "split": split,
        "has_amazon": amazon,
        "unresolved_count": len(res.unresolved),
        "resolved_count": len(items) - len(res.unresolved),
    }
