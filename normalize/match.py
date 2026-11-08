"""Category matching and brand tier classification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from normalize.categories import CategoryDef
from normalize.text import fold
from normalize.types import AttrValue, RawOffer


def phrase_regex(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(fold(phrase))}(?!\w)")


def exclude_regex(phrase: str) -> re.Pattern:
    """Like phrase_regex, but "peanut-free" / "peanut free" do not exclude "peanut"."""
    return re.compile(rf"(?<!\w){re.escape(fold(phrase))}(?!\w)(?![- ]free\b)")


def _forms(phrase: str, plural: bool) -> list[str]:
    base = fold(phrase)
    if not plural:
        return [base]
    forms = [base, base + "s"]
    if base.endswith("s") and len(base) > 1:
        forms.append(base[:-1])
    return forms


def find_category(
    text: str, categories: Mapping[str, CategoryDef], plural: bool = False
) -> tuple[str, tuple[int, int]] | None:
    """Category whose longest include phrase matches the folded ``text``, with that span.

    Excludes veto exactly as written. With ``plural`` each include phrase also matches
    with its last word singular or plural; the text itself is never rewritten.
    Ties between categories give None.
    """
    best_len = 0
    best: list[tuple[str, tuple[int, int]]] = []
    for slug, cat in categories.items():
        if any(exclude_regex(p).search(text) for p in cat.exclude):
            continue
        hits = [
            m.span()
            for p in cat.include
            for form in _forms(p, plural)
            if (m := phrase_regex(form).search(text))
        ]
        if not hits:
            continue
        span = max(hits, key=lambda s: s[1] - s[0])
        length = span[1] - span[0]
        if length > best_len:
            best_len, best = length, [(slug, span)]
        elif length == best_len:
            best.append((slug, span))
    return best[0] if len(best) == 1 else None


def match_category(raw: RawOffer, categories: Mapping[str, CategoryDef]) -> str | None:
    """Slug of the category whose longest include phrase matches; ties give None."""
    found = find_category(fold(f"{raw.title} | {raw.brand or ''}"), categories)
    return found[0] if found else None


def load_store_brands(path: Path) -> frozenset[str]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return frozenset(fold(str(b)) for b in data)


def brand_tier(raw: RawOffer, store_brands: frozenset[str]) -> AttrValue | None:
    brands = {fold(b) for b in store_brands}
    brand = fold(raw.brand or "")
    if brand:
        return AttrValue("store_brand" if brand in brands else "name_brand", "rule")
    title = fold(raw.title)
    for b in brands:
        if title.startswith(b) and not title[len(b):len(b) + 1].isalnum():
            return AttrValue("store_brand", "rule")
    return None
