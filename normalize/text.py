"""Text folding shared by every matcher. Pure: no I/O."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "″": '"'})
_SPACE = re.compile(r"\s+")


def fold(text: str) -> str:
    """NFKC, straight quotes, single spaces (NBSP included), stripped, casefolded."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _SPACE.sub(" ", text).strip().casefold()
