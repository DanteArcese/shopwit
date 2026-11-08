"""Free-text query resolution: category, attribute filters and leftover text. Pure."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from normalize.categories import AttributeDef, CategoryDef
from normalize.extract import cast_value
from normalize.match import find_category, phrase_regex
from normalize.text import fold

Span = tuple[int, int]


@dataclass(frozen=True)
class QueryResolution:
    category: str | None
    filters: dict[str, object]
    leftover: str


def _explicit_filter(attr: AttributeDef, text: str) -> tuple[object, list[Span]] | None:
    """Value and phrase spans from patterns, else synonyms; None if absent or ambiguous."""
    found: dict[object, list[Span]] = {}
    for pattern in attr.patterns:
        for m in pattern.finditer(text):
            cast = cast_value(attr, m.group(1))
            if cast is not None:
                found.setdefault(cast, []).append(m.span())
    if not found:
        for key, phrases in attr.synonyms.items():
            cast = cast_value(attr, key)
            if cast is None:
                continue
            for phrase in phrases:
                for m in phrase_regex(phrase).finditer(text):
                    found.setdefault(cast, []).append(m.span())
    if len(found) != 1:
        return None
    value, spans = next(iter(found.items()))
    if value is False:  # never filter on a bool being false
        return None
    return value, spans


def _remove(text: str, spans: list[Span]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = [" "] * (end - start)
    rest = " ".join("".join(chars).split())
    return rest if any(c.isalnum() for c in rest) else ""


def resolve_query(text: str, categories: Mapping[str, CategoryDef]) -> QueryResolution:
    folded = fold(text)
    if not folded:
        return QueryResolution(None, {}, "")
    found = find_category(folded, categories, plural=True)
    if found is None:
        return QueryResolution(None, {}, folded)
    slug, include_span = found
    cat = categories[slug]
    spans: list[Span] = [include_span]

    filters: dict[str, object] = {}
    for name, attr in cat.attributes.items():
        hit = _explicit_filter(attr, folded)
        if hit is not None:
            filters[name] = hit[0]
            spans += hit[1]
    return QueryResolution(slug, filters, _remove(folded, spans))
