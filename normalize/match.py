"""Category matching and brand tier classification."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml

from normalize.categories import CategoryDef
from normalize.types import AttrValue, RawOffer


def _phrase_re(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)


def match_category(raw: RawOffer, categories: Mapping[str, CategoryDef]) -> str | None:
    """Slug of the category whose longest include phrase matches; ties give None."""
    best_len = 0
    best: list[str] = []
    for slug, cat in categories.items():
        if any(_phrase_re(p).search(raw.title) for p in cat.exclude):
            continue
        matched = [len(p) for p in cat.include if _phrase_re(p).search(raw.title)]
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
    return frozenset(str(b).lower() for b in data)


def brand_tier(raw: RawOffer, store_brands: frozenset[str]) -> AttrValue | None:
    if raw.brand:
        tier = "store_brand" if raw.brand.strip().lower() in store_brands else "name_brand"
        return AttrValue(tier, "rule")
    title = raw.title.lower()
    for b in store_brands:
        if title.startswith(b) and not (title[len(b):len(b) + 1].isalnum()):
            return AttrValue("store_brand", "rule")
    return None
