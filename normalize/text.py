"""Text folding shared by every matcher. Pure: no I/O."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "″": '"'})
_SPACE = re.compile(r"\s+")
_TRADEMARK = re.compile("[\u2122\u2120]")  # TM, SM: NFKC would turn them into "tm"/"sm"
# Precomposed vulgar fractions: NFKC yields digit + U+2044, which a following "1"
# would glue into a different number ("1\u00bd" -> "11/2"). Expand them ourselves.
_VULGAR = re.compile("[\u00bc-\u00be\u2150-\u215f\u2189]")


def _expand_vulgar(match: re.Match[str]) -> str:
    frac = unicodedata.normalize("NFKC", match.group()).replace("\u2044", "/")
    return f" {frac} "


# U+2032/U+2033 (prime, double prime) NFKC-decompose to "'" / "''"; map them first so
# a double prime stays one inch mark.
_PRIMES = str.maketrans({"\u2032": "'", "\u2033": '"'})


def fold(text: str) -> str:
    """NFKC, straight quotes, single spaces (NBSP included), stripped, casefolded."""
    text = text.translate(_PRIMES)
    text = _VULGAR.sub(_expand_vulgar, _TRADEMARK.sub(" ", text))
    text = unicodedata.normalize("NFKC", text).replace("\u2044", "/").translate(_QUOTES)
    return _SPACE.sub(" ", text).strip().casefold()
