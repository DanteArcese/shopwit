"""Parse quantity text into canonical measures. Pure: no I/O."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pint

from normalize.categories import Dimension
from normalize.text import fold

Measures = dict[str, float]

_ureg = pint.UnitRegistry()

# One number: mixed "1 1/2", fraction "1/2", comma-grouped "1,500", decimal or integer.
# Never starts mid-number (after a word char, ".", "/", or a "<digit>,").
_START = r"(?<![\w./])(?<!\d,)"
_NUM = (
    rf"{_START}(\d+\s+\d+/[1-9]\d*|\d+/[1-9]\d*|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?|\.\d+)"
)


def _to_float(num: str) -> float:
    num = num.replace(",", "")
    if "/" in num:
        whole, _, frac = num.rpartition(" ")
        if not whole:
            whole = "0"
        a, b = frac.split("/")
        return float(whole) + float(a) / float(b)
    return float(num)

# --- multipacks --------------------------------------------------------------
_MULT_PREFIX = re.compile(r"(\d+)\s*x\s*$", re.IGNORECASE)
_PACK_N = re.compile(r"\b(\d+)\s*-?\s*(?:pack|pk)\b", re.IGNORECASE)
# "2 x 12" is a multiplier; "11 x 10.4 in" / '11 x 10.4"' is a sheet size, not one.
_MULT_PREFIX_ANY = re.compile(
    r'(?<![\w.])\d+\s*x\s*(?!\d+(?:\.\d+)?\s*(?:in\b|"))\d', re.IGNORECASE
)
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
    return re.compile(rf"{_NUM}\s*(?:{alts})(?![A-Za-z])", re.IGNORECASE)


_VOLUME_RE = _unit_regex(_VOLUME_UNITS)
_MASS_RE = _unit_regex(_MASS_UNITS)


_RANGE_BEFORE = re.compile(r"\d\s*(?:[-\u2013]|\bto\b)\s*$", re.IGNORECASE)


def _ambiguous_lead(text: str, m: re.Match[str]) -> bool:
    """True when the number is the tail of a range ("1-2", "1 - 2", "1\u20132", "1 to 2")
    or a hyphenated fraction ("1-1/2"). Ambiguous, so callers must not guess."""
    if m.group(1) is None:
        return False
    return _RANGE_BEFORE.search(text[: m.start(1)]) is not None


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
    if not m or _ambiguous_lead(text, m):
        return None
    unit = next(units[i][1] for i in range(len(units)) if m.group(f"u{i}"))
    value = _to_float(m.group(1)) * _multiplier(text, m.start())
    if value <= 0:
        return None
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
    rf"{_NUM}\s*(?:ct|count|eggs?|pieces?|pcs)\b", re.IGNORECASE
)
_DOZEN_RE = re.compile(rf"(?:{_NUM}\s*)?dozen\b", re.IGNORECASE)


def _count(text: str) -> Measures:
    m = _DOZEN_RE.search(text)
    if m:
        if _ambiguous_lead(text, m):
            return {}
        if m.group(1) is None and re.search(r"(?:\d[,./]?|\))\s*$", text[: m.start()]):
            return {}  # an unparsable number precedes "dozen": never guess
        value = 12 * (_to_float(m.group(1)) if m.group(1) else 1.0)
        return {"count": value} if value > 0 else {}
    m = _COUNT_RE.search(text)
    if m:
        if _ambiguous_lead(text, m):
            return {}
        value = _to_float(m.group(1)) * _multiplier(text, m.start())
        return {"count": value} if value > 0 else {}
    return {}


# --- paper -------------------------------------------------------------------
_ROLL_ADJECTIVES = (
    "mega|double|triple|super|giant|jumbo|regular|family|big|huge|ultra|standard|plus"
)
_ROLLS_RE = re.compile(
    rf"{_START}(\d+)\s+(?:(?:{_ROLL_ADJECTIVES})\s+)*rolls?\b", re.IGNORECASE
)
_CNT = rf"{_START}(\d{{1,3}}(?:,\d{{3}})+|\d+)"
_SHEETS_PER_ROLL_RE = re.compile(rf"{_CNT}\s*sheets?\s*(?:per|/)\s*roll\b", re.IGNORECASE)
_SHEETS_TOTAL_RE = re.compile(
    rf"{_CNT}\s*sheets?\s+(?:total|in\s+all)\b", re.IGNORECASE
)
_SHEETS_BARE_RE = re.compile(rf"{_CNT}\s*sheets?\b", re.IGNORECASE)
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
    if _PACK_N.search(text) or _PACK_OF.search(text) or _MULT_PREFIX_ANY.search(text):
        return {}  # multipack: roll counts are ambiguous (per pack or total), never guess
    m = _ROLLS_RE.search(text)
    if not m:
        return {}
    rolls = float(m.group(1))
    out: Measures = {"rolls": rolls}

    sheets = None
    per_roll = _SHEETS_PER_ROLL_RE.search(text)
    total = _SHEETS_TOTAL_RE.search(text)
    if per_roll:
        sheets = rolls * _to_float(per_roll.group(1))
    elif total:
        sheets = _to_float(total.group(1))
    else:
        # "380 Sheets, 30 Rolls": a bare sheet count beside a roll count is per roll.
        bare = _SHEETS_BARE_RE.search(text)
        if bare:
            sheets = rolls * _to_float(bare.group(1))
    if sheets == 0:
        sheets = None
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
        result = parse(fold(text or ""))
        if _REQUIRED[dimension] in result:
            return result
    return {}
