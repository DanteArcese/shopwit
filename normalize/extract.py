"""Deterministic attribute extraction from raw offers."""

from __future__ import annotations

import re

from normalize.categories import AttributeDef, CategoryDef
from normalize.text import fold
from normalize.types import AttrValue, OffProduct, RawOffer

_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}


def _cast(attr: AttributeDef, value: object) -> str | int | bool | None:
    """Cast ``value`` to the attribute's type; None if invalid or not a declared value."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    if attr.type == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        return None
    if attr.type == "int":
        try:
            result: str | int = int(str(value))
        except ValueError:
            return None
    else:
        result = str(value)
        if result not in attr.values:
            result = result.lower()  # pattern captures keep the title's casing
    if attr.values and result not in attr.values:
        return None
    return result


def _collect(attr: AttributeDef, text: str) -> set[str | int | bool]:
    found: set[str | int | bool] = set()
    for pattern in attr.patterns:
        for match in pattern.finditer(text):
            cast = _cast(attr, match.group(1))
            if cast is not None:
                found.add(cast)
    return found


def _collect_synonyms(attr: AttributeDef, text: str) -> set[str | int | bool]:
    found: set[str | int | bool] = set()
    for key, phrases in attr.synonyms.items():
        cast = _cast(attr, key)
        if cast is None:
            continue
        for phrase in phrases:
            if re.search(rf"(?<!\w){re.escape(fold(phrase))}(?!\w)", text):
                found.add(cast)
                break
    return found


def extract_attributes(
    raw: RawOffer, category: CategoryDef, off: OffProduct | None
) -> tuple[dict[str, AttrValue], list[str]]:
    text = fold(f"{raw.title} {raw.brand or ''} {raw.size_text or ''}")
    attrs: dict[str, AttrValue] = {}
    unresolved: list[str] = []

    for name, attr in category.attributes.items():
        if name in raw.fields:
            cast = _cast(attr, raw.fields[name])
            if cast is not None:
                attrs[name] = AttrValue(cast, "api")
                continue

        if attr.type == "bool" and attr.off_label and off and attr.off_label in off.labels:
            attrs[name] = AttrValue(True, "off")
            continue

        found = _collect(attr, text)
        if not found:
            found = _collect_synonyms(attr, text)
        if len(found) == 1:
            attrs[name] = AttrValue(next(iter(found)), "rule")
            continue
        if len(found) > 1:
            unresolved.append(f"attribute:{name}:ambiguous")
            continue

        if attr.type == "bool" and attr.absent is not None:
            attrs[name] = AttrValue(attr.absent, "rule")
        else:
            unresolved.append(f"attribute:{name}")

    return attrs, unresolved
