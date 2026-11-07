"""Parse quantity text into canonical measures. Pure: no I/O."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pint

from normalize.categories import Dimension

Measures = dict[str, float]

_ureg = pint.UnitRegistry()

_NUM = r"(\d+(?:\.\d+)?|\.\d+)"

# --- multipacks --------------------------------------------------------------
_MULT_PREFIX = re.compile(r"(\d+)\s*x\s*$", re.IGNORECASE)
_PACK_N = re.compile(r"\b(\d+)\s*-?\s*(?:pack|pk)\b", re.IGNORECASE)
_PACK_OF = re.compile(r"\bpack\s+of\s+(\d+)\b", re.IGNORECASE)

# --- volume / mass -----------------------------------------------------------
# (regex alternation, pint unit) pairs; longer spellings first.
_VOLUME_UNITS = [
    (r"fl\.?\s*oz\.?|fz|fluid\s+ounces?", "fluid_ounce"),
    (r"gal(?:lons?)?", "gallon"),
    (r"qt|quarts?", "quart"),
    (r"pt|pints?", "pint"),
    (r"ml|milliliters?|millilitres?", "milliliter"),
    (r"l|liters?|litres?", "liter"),
    (r"oz", "fluid_ounce"),  # bare oz is fluid in a volume category
]
_MASS_UNITS = [
    (r"oz|ounces?", "ounce"),
    (r"lbs?|pounds?", "pound"),
    (r"kg|kilograms?", "kilogram"),
    (r"g|grams?", "gram"),
]


def _unit_regex(units: list[tuple[str, str]]) -> re.Pattern[str]:
    alts = "|".join(f"(?P<u{i}>{pat})" for i, (pat, _) in enumerate(units))
    return re.compile(rf"(?<![\w.]){_NUM}\s*(?:{alts})(?![A-Za-z])", re.IGNORECASE)


_VOLUME_RE = _unit_regex(_VOLUME_UNITS)
_MASS_RE = _unit_regex(_MASS_UNITS)


def _multiplier(text: str, start: int) -> int:
    m = _MULT_PREFIX.search(text[:start])
    if m:
        return int(m.group(1))
    m = _PACK_OF.search(text) or _PACK_N.search(text)
    return int(m.group(1)) if m else 1


def _measure(
    text: str, rx: re.Pattern[str], units: list[tuple[str, str]], target: str
) -> float | None:
    m = rx.search(text)
    if not m:
        return None
    unit = next(units[i][1] for i in range(len(units)) if m.group(f"u{i}"))
    value = float(m.group(1)) * _multiplier(text, m.start())
    return float((value * _ureg(unit)).to(target).magnitude)


def _volume(text: str) -> Measures:
    ml = _measure(text, _VOLUME_RE, _VOLUME_UNITS, "milliliter")
    return {} if ml is None else {"volume_ml": ml}


def _mass(text: str) -> Measures:
    g = _measure(_strip_fl_oz(text), _MASS_RE, _MASS_UNITS, "gram")
    return {} if g is None else {"mass_g": g}


def _strip_fl_oz(text: str) -> str:
    return re.sub(r"fl\.?\s*oz\.?|fz", " ", text, flags=re.IGNORECASE)


# --- count -------------------------------------------------------------------
_COUNT_RE = re.compile(
    r"(?<![\w.])(\d+)\s*(?:ct|count|eggs?|pieces?|pcs)\b", re.IGNORECASE
)
_DOZEN_RE = re.compile(r"(?<![\w.])(?:(\d+(?:\.\d+)?)\s*)?dozen\b", re.IGNORECASE)


def _count(text: str) -> Measures:
    m = _DOZEN_RE.search(text)
    if m:
        return {"count": float(m.group(1) or 1) * 12}
    m = _COUNT_RE.search(text)
    if m:
        return {"count": float(m.group(1)) * _multiplier(text, m.start())}
    return {}


# --- paper -------------------------------------------------------------------
_ROLL_ADJECTIVES = (
    "mega|double|triple|super|giant|jumbo|regular|family|big|huge|ultra|standard|plus"
)
_ROLLS_RE = re.compile(
    rf"(?<![\w.])(\d+)\s+(?:(?:{_ROLL_ADJECTIVES})\s+)*rolls?\b", re.IGNORECASE
)
_SHEETS_PER_ROLL_RE = re.compile(r"(\d+)\s*sheets?\s*(?:per|/)\s*roll\b", re.IGNORECASE)
_SHEETS_BARE_RE = re.compile(r"(?<![\w.])(\d+)\s*(?:-\s*)?sheets?\b", re.IGNORECASE)
_SHEET_SIZE_RE = re.compile(
    rf'{_NUM}\s*(?:in\b|")?\s*x\s*{_NUM}\s*(?:in\b|")', re.IGNORECASE
)
_AREA_SQFT_RE = re.compile(
    rf"{_NUM}\s*(?:sq\.?\s*ft\.?|square\s+feet|sf)\b", re.IGNORECASE
)


# "= 72 Regular Rolls" marketing claim, up to the next comma/semicolon.
_EQUIVALENCE_RE = re.compile(r"=[^,;]*")


def _paper(text: str) -> Measures:
    text = _EQUIVALENCE_RE.sub(" ", text)
    m = _ROLLS_RE.search(text)
    if not m:
        return {}
    rolls = float(m.group(1))
    out: Measures = {"rolls": rolls}

    per_roll = _SHEETS_PER_ROLL_RE.search(text)
    # "380 Sheets, 30 Rolls": a bare sheet count beside a roll count is per roll.
    per_roll = per_roll or _SHEETS_BARE_RE.search(text)
    sheets = rolls * float(per_roll.group(1)) if per_roll else None
    if sheets is not None:
        out["sheets"] = sheets

    area = _AREA_SQFT_RE.search(text)
    size = _SHEET_SIZE_RE.search(text)
    if area:
        out["area_m2"] = float(
            (float(area.group(1)) * _ureg.foot**2).to("meter**2").magnitude
        )
    elif sheets is not None and size:
        in2 = float(size.group(1)) * float(size.group(2)) * sheets
        out["area_m2"] = float((in2 * _ureg.inch**2).to("meter**2").magnitude)
    return out


_PARSERS = {"volume": _volume, "mass": _mass, "count": _count, "paper": _paper}
_REQUIRED = {
    "volume": "volume_ml",
    "mass": "mass_g",
    "count": "count",
    "paper": "rolls",
}


def parse_measures(texts: Sequence[str], dimension: Dimension) -> Measures:
    """Return canonical measures from the first text that yields the dimension's key."""
    parse = _PARSERS[dimension]
    for text in texts:
        result = parse(text or "")
        if _REQUIRED[dimension] in result:
            return result
    return {}
