"""Manual attribute overrides (filled in by a later task)."""

from __future__ import annotations

from normalize.types import NormalizedOffer


def apply(product, normalized: NormalizedOffer) -> NormalizedOffer:
    return normalized
