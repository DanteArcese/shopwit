"""Free-text query resolution: category, attribute filters and leftover text. Pure."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from normalize.categories import AttributeDef, CategoryDef
from normalize.extract import _cast
from normalize.match import _phrase_re, match_category
from normalize.text import fold
from normalize.types import RawOffer

Span = tuple[int, int]


@dataclass(frozen=True)
class QueryResolution:
    category: str | None
    filters: dict[str, object]
    leftover: str


def _match(text: str, categories: Mapping[str, CategoryDef]) -> str | None:
    raw = RawOffer(
        retailer="", retailer_item_id="", title=text, price=Decimal(0), currency="USD",
        url="", fetched_at=datetime.now(timezone.utc),
    )
    return match_category(raw, categories)


def _find_category(
    text: str, categories: Mapping[str, CategoryDef]
) -> tuple[str, str] | None:
    """Category slug and the (possibly plural-adjusted) text it matched on."""
    words = text.split()
    variants = [text]
    variants += [
        " ".join(words[:i] + [w[:-1]] + words[i + 1:])
        for i, w in enumerate(words) if w.endswith("s") and len(w) > 1
    ]
    variants += [" ".join(words[:i] + [w + "s"] + words[i + 1:]) for i, w in enumerate(words)]
    for variant in variants:
        slug = _match(variant, categories)
        if slug is not None:
            return slug, variant
    return None


def _explicit_filter(attr: AttributeDef, text: str) -> tuple[object, list[Span]] | None:
    """Value and phrase spans from patterns, else synonyms; None if absent or ambiguous."""
    found: dict[object, list[Span]] = {}
    for pattern in attr.patterns:
        for m in pattern.finditer(text):
            cast = _cast(attr, m.group(1))
            if cast is not None:
                found.setdefault(cast, []).append(m.span())
    if not found:
        for key, phrases in attr.synonyms.items():
            cast = _cast(attr, key)
            if cast is None:
                continue
            for phrase in phrases:
                for m in _phrase_re(phrase).finditer(text):
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
    return " ".join("".join(chars).split())


def resolve_query(text: str, categories: Mapping[str, CategoryDef]) -> QueryResolution:
    folded = fold(text)
    if not folded:
        return QueryResolution(None, {}, "")
    found = _find_category(folded, categories)
    if found is None:
        return QueryResolution(None, {}, folded)
    slug, matched_text = found
    cat = categories[slug]

    spans: list[Span] = []
    longest = max(
        (fold(p) for p in cat.include if _phrase_re(p).search(matched_text)), key=len
    )
    spans += [m.span() for m in _phrase_re(longest).finditer(matched_text)]

    filters: dict[str, object] = {}
    for name, attr in cat.attributes.items():
        hit = _explicit_filter(attr, matched_text)
        if hit is not None:
            filters[name] = hit[0]
            spans += hit[1]
    return QueryResolution(slug, filters, _remove(matched_text, spans))
