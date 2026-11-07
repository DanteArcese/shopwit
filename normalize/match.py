"""Category matching and brand tier classification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from normalize.categories import CategoryDef
from normalize.text import fold
from normalize.types import AttrValue, RawOffer


def _phrase_re(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(fold(phrase))}(?!\w)")


def match_category(raw: RawOffer, categories: Mapping[str, CategoryDef]) -> str | None:
    """Slug of the category whose longest include phrase matches; ties give None."""
    text = fold(f"{raw.title} | {raw.brand or ''}")
    best_len = 0
    best: list[str] = []
    for slug, cat in categories.items():
        if any(_phrase_re(p).search(text) for p in cat.exclude):
            continue
        matched = [len(fold(p)) for p in cat.include if _phrase_re(p).search(text)]
        if not matched:
            continue
        longest = max(matched)
        if longest > best_len:
            best_len, best = longest, [slug]
        elif longest == best_len:
            best.append(slug)
    return best[0] if len(best) == 1 else None


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
