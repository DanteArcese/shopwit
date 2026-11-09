"""Category definitions loaded from YAML files."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

Dimension = Literal["volume", "mass", "count", "paper", "laundry", "tissue"]

KNOWN_COMPARISONS: frozenset[str] = frozenset(
    {"per_volume", "per_mass", "per_count", "area_x_ply", "per_100_sheets", "per_roll", "per_load", "sheets_x_ply"}
)

_ATTR_TYPES = ("int", "enum", "bool")
_TOP_KEYS = {"name", "parent", "dimension", "match", "attributes", "comparison", "search"}
_ATTR_KEYS = {"type", "values", "patterns", "synonyms", "off_label", "absent"}


class CategoryError(ValueError):
    """Invalid category file; the message is prefixed by the file path."""


@dataclass(frozen=True)
class AttributeDef:
    name: str
    type: Literal["int", "enum", "bool"]
    values: tuple[str | int, ...] = ()
    patterns: tuple[re.Pattern, ...] = ()
    synonyms: Mapping[str | int | bool, tuple[str, ...]] = field(default_factory=dict)
    off_label: str | None = None
    absent: bool | None = None


@dataclass(frozen=True)
class CategoryDef:
    slug: str
    name: str
    parent: str | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    dimension: Dimension
    attributes: Mapping[str, AttributeDef]
    comparison_default: str
    comparison_alternates: tuple[str, ...]
    search: tuple[str, ...] = ()


def _check_keys(data: Any, allowed: set[str], what: str, fail) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        fail(f"{what} must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        fail(f"unknown key(s) in {what}: {', '.join(sorted(map(str, unknown)))}")
    return data


def _str_list(value: Any, what: str, fail) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        fail(f"{what} must be a list of strings")
    return tuple(value)


def _parse_attribute(name: str, data: Any, fail) -> AttributeDef:
    where = f"attribute {name!r}"
    data = _check_keys(data, _ATTR_KEYS, where, fail)
    atype = data.get("type")
    if atype not in _ATTR_TYPES:
        fail(f"{where}: type must be one of {', '.join(_ATTR_TYPES)}")

    raw_values = data.get("values") or []
    if not isinstance(raw_values, list):
        fail(f"{where}: values must be a list")
    if atype == "enum":
        if not raw_values:
            fail(f"{where}: enum requires values")
        values: tuple[str | int, ...] = tuple(str(v) for v in raw_values)
    elif atype == "int":
        try:
            values = tuple(int(v) for v in raw_values)
        except (TypeError, ValueError):
            fail(f"{where}: int values must be integers")
    else:
        values = ()

    patterns = []
    for p in _str_list(data.get("patterns"), f"{where}: patterns", fail):
        try:
            compiled = re.compile(p, re.IGNORECASE)
        except re.error as e:
            fail(f"{where}: invalid regex {p!r}: {e}")
        if compiled.groups != 1:
            fail(f"{where}: pattern {p!r} must have exactly one capture group")
        patterns.append(compiled)

    synonyms: dict[str | int | bool, tuple[str, ...]] = {}
    raw_syn = data.get("synonyms") or {}
    if not isinstance(raw_syn, dict):
        fail(f"{where}: synonyms must be a mapping")
    for key, words in raw_syn.items():
        if atype == "enum":
            norm: str | int | bool = str(key)
            if norm not in values:
                fail(f"{where}: synonym key {key!r} is not a declared value")
        elif atype == "int":
            try:
                norm = int(key)
            except (TypeError, ValueError):
                fail(f"{where}: synonym key {key!r} must be an integer")
            if values and norm not in values:
                fail(f"{where}: synonym key {key!r} is not a declared value")
        else:
            if not isinstance(key, bool):
                fail(f"{where}: bool synonym key {key!r} must be true or false")
            norm = key
        synonyms[norm] = _str_list(words, f"{where}: synonyms for {key!r}", fail)

    off_label = data.get("off_label")
    if off_label is not None and not isinstance(off_label, str):
        fail(f"{where}: off_label must be a string")
    absent = data.get("absent")
    if absent is not None and not isinstance(absent, bool):
        fail(f"{where}: absent must be true, false or null")

    return AttributeDef(
        name=name,
        type=atype,
        values=values,
        patterns=tuple(patterns),
        synonyms=synonyms,
        off_label=off_label,
        absent=absent,
    )


def _load_file(path: Path, slug: str) -> CategoryDef:
    def fail(msg: str):
        raise CategoryError(f"{path}: {msg}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        fail(f"invalid YAML: {e}")
    data = _check_keys(data, _TOP_KEYS, "top level", fail)

    name = data.get("name")
    if not isinstance(name, str) or not name:
        fail("name is required")
    parent = data.get("parent")
    if parent is not None and not isinstance(parent, str):
        fail("parent must be a string or null")
    dimension = data.get("dimension")
    if dimension not in get_args(Dimension):
        fail(f"dimension must be one of {', '.join(get_args(Dimension))}")

    match = _check_keys(data.get("match"), {"include", "exclude"}, "match", fail)
    include = _str_list(match.get("include"), "match.include", fail)
    exclude = _str_list(match.get("exclude"), "match.exclude", fail)

    attrs = data.get("attributes") or {}
    if not isinstance(attrs, dict):
        fail("attributes must be a mapping")
    attributes = {
        str(k): _parse_attribute(str(k), v, fail) for k, v in attrs.items()
    }

    comp = _check_keys(data.get("comparison"), {"default", "alternates"}, "comparison", fail)
    default = comp.get("default")
    if default not in KNOWN_COMPARISONS:
        fail(f"unknown comparison default {default!r}")
    alternates = _str_list(comp.get("alternates"), "comparison.alternates", fail)
    for alt in alternates:
        if alt not in KNOWN_COMPARISONS:
            fail(f"unknown comparison alternate {alt!r}")

    search = _str_list(data.get("search"), "search", fail) or (name,)

    return CategoryDef(
        slug=slug,
        name=name,
        parent=parent,
        include=include,
        exclude=exclude,
        dimension=dimension,
        attributes=attributes,
        comparison_default=default,
        comparison_alternates=alternates,
        search=search,
    )


def load_categories(root: Path) -> dict[str, CategoryDef]:
    """Load every ``*.yaml`` under ``root``; the slug is the relative path sans extension."""
    root = Path(root)
    result: dict[str, CategoryDef] = {}
    for path in sorted(root.rglob("*.yaml")):
        slug = path.relative_to(root).with_suffix("").as_posix()
        result[slug] = _load_file(path, slug)
    return result
