"""Generic measure and phrase normalization for open-ended search. Pure: no I/O.

Never guesses: a bare "oz" is its own dimension, nothing is compared across
dimensions, and anything ambiguous is unknown with a reason.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from normalize.quantity import parse_generic, unit_registry
from normalize.text import fold

Dimension = Literal["volume", "mass", "oz_labeled", "count"]
DIMENSION_ORDER: tuple[Dimension, ...] = ("volume", "mass", "oz_labeled", "count")

_KINDS: tuple[tuple[str, Dimension], ...] = (
    ("volume_ml", "volume"),
    ("mass_g", "mass"),
    ("oz_labeled", "oz_labeled"),
)


@dataclass(frozen=True)
class GenericMeasure:
    dimension: Dimension | None
    amount: float | None
    reason: str | None


def _unknown(reason: str) -> GenericMeasure:
    return GenericMeasure(None, None, reason)


def _oz_to(unit: str, amount: float) -> float:
    return float((amount * unit_registry().ounce).to(unit).magnitude)


def generic_measure(texts: Sequence[str], structured_unit: str | None = None) -> GenericMeasure:
    """One generic measure from [size_text, title, off_quantity] (missing as "")."""
    for i, text in enumerate(texts):
        p = parse_generic(fold(text or ""))
        found = [(key, dim) for key, dim in _KINDS if key in p]
        if len(found) >= 2 or "mixed" in p:
            return _unknown("more than one kind of size")
        if found:
            key, dim = found[0]
            amount = p[key]
            if i == 2 and dim == "oz_labeled":
                if structured_unit == "g":
                    return GenericMeasure("mass", _oz_to("gram", amount), None)
                if structured_unit == "ml":
                    # the quantity was labeled oz; ml says it is volume (fluid ounces)
                    ml = float((amount * unit_registry().fluid_ounce).to("milliliter").magnitude)
                    return GenericMeasure("volume", ml, None)
            return GenericMeasure(dim, amount, None)
        if "count" in p and "has_size_unit" not in p:
            return GenericMeasure("count", p["count"], None)
    return _unknown("size unknown")


def generic_unit_price(price: Decimal, measure: GenericMeasure) -> Decimal | None:
    if measure.dimension is None or not measure.amount:
        return None
    return (price / Decimal(str(measure.amount))).quantize(Decimal("0.000001"))


_WS = re.compile(r"\s+")


def normalize_phrase(text: str) -> str:
    return _WS.sub(" ", fold(text))[:100].strip()


_DIGIT_RUN = re.compile(r"\d(?:[\s\-.()]?\d){4,}")


def is_personal(phrase: str) -> bool:
    """An email-like or long digit-run phrase (phone, card, ZIP) is never stored or sent."""
    return "@" in phrase or _DIGIT_RUN.search(phrase) is not None


def off_category_path(tags: Sequence[str]) -> str | None:
    parts = []
    for tag in tags:
        if not tag.startswith("en:"):
            continue
        name = tag[3:].replace("-", " ").strip()
        if name:
            parts.append(name[0].upper() + name[1:])
    if not parts:
        return None
    return ("Open Food Facts/" + "/".join(parts))[:500]


def _trigrams(text: str) -> set[str]:
    grams: set[str] = set()
    for word in re.split(r"[\W_]+", text.lower()):
        if word:
            padded = f"  {word} "
            grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def trigram_similarity(a: str, b: str) -> float:
    """pg_trgm's similarity() in Python."""
    ta, tb = _trigrams(a), _trigrams(b)
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0
