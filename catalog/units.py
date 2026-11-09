"""Unit display and money formatting for the web UI.

Stored unit prices are per canonical unit (mL, g, item, m2*ply, 100 sheets,
roll). This module converts them for display in the viewer's unit system and
formats amounts. Money stays Decimal throughout.
"""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

from normalize.compare import COMPARISONS
from normalize.quantity import unit_registry

ONE = Decimal(1)

def _exact(unit: str, target: str) -> Decimal:
    """Pint's own conversion (the registry ingestion uses), as an exact-ish Decimal."""
    return Decimal(format(float((1 * unit_registry()(unit)).to(target).magnitude), ".12g"))


ML_PER_FL_OZ = _exact("fluid_ounce", "milliliter")
G_PER_OZ = _exact("ounce", "gram")
M2_PER_SQ_FT = Decimal("0.09290304")  # exact by definition of the foot
FL_OZ_PER_GAL = 128
OZ_PER_LB = 16

# (comparison, unit_system) -> (label, factor): price per canonical unit * factor
# = price per displayed unit.
DISPLAY: dict[tuple[str, str], tuple[str, Decimal]] = {
    ("per_volume", "imperial"): ("fl oz", ML_PER_FL_OZ),
    ("per_volume", "metric"): ("L", Decimal("1000")),
    ("per_mass", "imperial"): ("oz", G_PER_OZ),
    ("per_mass", "metric"): ("kg", Decimal("1000")),
    ("per_count", "imperial"): ("each", ONE),
    ("per_count", "metric"): ("each", ONE),
    ("area_x_ply", "imperial"): ("100 sq ft·ply", M2_PER_SQ_FT * 100),
    ("area_x_ply", "metric"): ("m²·ply", ONE),
    ("per_100_sheets", "imperial"): ("100 sheets", ONE),
    ("per_100_sheets", "metric"): ("100 sheets", ONE),
    ("per_roll", "imperial"): ("roll", ONE),
    ("per_roll", "metric"): ("roll", ONE),
    ("sheets_x_ply", "imperial"): ("100 sheets·ply", ONE),
    ("sheets_x_ply", "metric"): ("100 sheets·ply", ONE),
    ("per_load", "imperial"): ("load", ONE),
    ("per_load", "metric"): ("load", ONE),
    ("per_oz_labeled", "imperial"): ("oz (as labeled)", ONE),
    ("per_oz_labeled", "metric"): ("oz (as labeled)", ONE),
}

# Generic dimension -> comparison key used by DISPLAY.
GENERIC_COMPARISON: dict[str, str] = {
    "volume": "per_volume",
    "mass": "per_mass",
    "count": "per_count",
    "oz_labeled": "per_oz_labeled",
}

def display_unit_price(unit_price: Decimal, comparison: str, unit_system: str) -> tuple[Decimal, str]:
    """Convert a canonical unit price to (price, label) for the unit system.

    Unknown combinations fall back to the canonical price and label so the UI
    never crashes.
    """
    entry = DISPLAY.get((comparison, unit_system))
    if entry is None:
        known = COMPARISONS.get(comparison)
        return unit_price, known.unit_label if known else ""
    label, factor = entry
    return unit_price * factor, label


def format_money(amount: Decimal, currency: str) -> str:
    """Format money: 2 decimals at 1 or more, otherwise 3 significant digits.

    Amounts under 1 keep at least 2 decimals ($0.50) and a non-zero amount is
    never shown as $0.00. USD uses "$"; other currencies are prefixed by code.
    Negative amounts are rejected.
    """
    if amount < 0:
        raise ValueError("negative amount")
    if amount >= 1 or amount == 0:
        text = f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"
    else:
        step = Decimal(1).scaleb(amount.adjusted() - 2)  # 3 significant digits
        rounded = amount.quantize(step, rounding=ROUND_HALF_UP)
        if rounded >= 1:  # e.g. 0.9996 rounded up to 1
            text = f"{rounded.quantize(Decimal('0.01')):,}"
        else:
            text = f"{rounded:f}"
            whole, _, frac = text.partition(".")
            frac = frac.rstrip("0").ljust(2, "0")
            text = f"{whole}.{frac}"
    return f"${text}" if currency == "USD" else f"{currency} {text}"


def size_to_canonical(value: Decimal, dimension: str, unit_system: str) -> float:
    """Convert a size-filter input to canonical units (mL, g, m2, count).

    Imperial: fl oz, oz, sq ft. Metric: L, kg, m2. Count is unchanged.
    """
    imperial = unit_system == "imperial"
    if dimension == "volume":
        result = value * ML_PER_FL_OZ if imperial else value * 1000
    elif dimension == "mass":
        result = value * G_PER_OZ if imperial else value * 1000
    elif dimension == "paper":
        result = value * M2_PER_SQ_FT if imperial else value
    else:
        result = value
    return float(result)


def _num(value: Decimal) -> str:
    """At most 2 decimals, trailing zeros trimmed, thousands separators."""
    text = f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


def _volume(ml: Decimal, imperial: bool) -> str:
    if imperial:
        fl_oz = ml / ML_PER_FL_OZ
        text = f"{_num(fl_oz)} fl oz"
        if fl_oz.quantize(ONE, rounding=ROUND_HALF_UP) >= FL_OZ_PER_GAL:
            text += f" ({_num(fl_oz / FL_OZ_PER_GAL)} gal)"
        return text
    return f"{_num(ml / 1000)} L" if ml >= 1000 else f"{_num(ml)} mL"


def _mass(grams: Decimal, imperial: bool) -> str:
    if imperial:
        oz = grams / G_PER_OZ
        text = f"{_num(oz)} oz"
        if oz.quantize(ONE, rounding=ROUND_HALF_UP) >= OZ_PER_LB:
            text += f" ({_num(oz / OZ_PER_LB)} lb)"
        return text
    return f"{_num(grams / 1000)} kg" if grams >= 1000 else f"{_num(grams)} g"


def _paper(measures: dict) -> str:
    parts = []
    rolls = measures.get("rolls")
    if rolls is not None:
        parts.append(f"{_num(_dec(rolls))} {'roll' if _dec(rolls) == 1 else 'rolls'}")
    sheets = measures.get("sheets")
    if sheets is not None:
        parts.append(f"{_num(_dec(sheets))} {'sheet' if _dec(sheets) == 1 else 'sheets'}")
    return " · ".join(parts)


def _laundry(measures: dict, imperial: bool) -> str:
    parts = []
    loads = measures.get("loads")
    if loads is not None:
        parts.append(f"{_num(_dec(loads))} {'load' if _dec(loads) == 1 else 'loads'}")
    if measures.get("volume_ml") is not None:
        parts.append(_volume(_dec(measures["volume_ml"]), imperial))
    if measures.get("mass_g") is not None:
        parts.append(_mass(_dec(measures["mass_g"]), imperial))
    if measures.get("count") is not None:
        parts.append(f"{_num(_dec(measures['count']))} ct")
    return " · ".join(parts)


def _tissue(measures: dict) -> str:
    sheets = measures.get("sheets")
    if sheets is None:
        return ""
    text = f"{_num(_dec(sheets))} sheets"
    boxes = measures.get("boxes")
    if boxes is None:
        return text
    return f"{_num(_dec(boxes))} {'box' if _dec(boxes) == 1 else 'boxes'} · {text}"


def display_size(measures: dict, dimension: str, unit_system: str) -> str:
    """Human-readable pack size, or "" when the relevant measure is missing.

    Volume: imperial fl oz (also gal from 128 fl oz), metric mL under 1000
    else L. Mass: imperial oz (also lb from 16 oz), metric g under 1000 else
    kg. Count: "12 ct". Paper: "18 rolls · 4,356 sheets", omitting missing
    parts. Numbers show at most 2 decimals, trimmed, with thousands separators.
    """
    imperial = unit_system == "imperial"
    if dimension == "paper":
        return _paper(measures)
    if dimension == "laundry":
        return _laundry(measures, imperial)
    if dimension == "tissue":
        return _tissue(measures)
    key = {"volume": "volume_ml", "mass": "mass_g", "count": "count"}.get(dimension)
    raw = measures.get(key) if key else None
    if raw is None:
        return ""
    value = _dec(raw)
    if dimension == "volume":
        return _volume(value, imperial)
    if dimension == "mass":
        return _mass(value, imperial)
    return f"{_num(value)} ct"


def display_generic_size(dimension: str, amount: float, unit_system: str) -> str:
    """Pack size for a generic measure; labeled oz stays "N oz", never converted."""
    if dimension == "oz_labeled":
        return f"{_num(_dec(amount))} oz"
    key = {"volume": "volume_ml", "mass": "mass_g", "count": "count"}[dimension]
    return display_size({key: amount}, dimension, unit_system)


# ---- list targets: "I want N <unit>" ------------------------------------------------------

TARGET_UNITS: dict[str, dict[str, tuple[str, str, Decimal]]] = {
    "volume": {
        "floz": ("fl oz", "volume_ml", ML_PER_FL_OZ),
        "gal": ("gal", "volume_ml", ML_PER_FL_OZ * FL_OZ_PER_GAL),
        "ml": ("mL", "volume_ml", Decimal(1)),
        "l": ("L", "volume_ml", Decimal(1000)),
    },
    "mass": {
        "oz": ("oz", "mass_g", G_PER_OZ),
        "lb": ("lb", "mass_g", G_PER_OZ * OZ_PER_LB),
        "g": ("g", "mass_g", Decimal(1)),
        "kg": ("kg", "mass_g", Decimal(1000)),
    },
    "count": {"ct": ("ct", "count", Decimal(1))},
    "paper": {
        "rolls": ("rolls", "rolls", Decimal(1)),
        "sheets": ("sheets", "sheets", Decimal(1)),
    },
    "laundry": {"loads": ("loads", "loads", Decimal(1))},
    "tissue": {"sheets": ("sheets", "sheets", Decimal(1))},
}

_TARGET_QUANTUM = Decimal("0.001")
_TARGET_MAX = Decimal("1000000")

_SYSTEM_UNITS = {
    ("volume", "imperial"): ["floz", "gal"],
    ("volume", "metric"): ["l", "ml"],
    ("mass", "imperial"): ["oz", "lb"],
    ("mass", "metric"): ["g", "kg"],
}


def target_units_for(dimension: str, unit_system: str) -> list[str]:
    """Unit codes offered for a target in the viewer's unit system."""
    if dimension == "count":
        return ["ct"]
    if dimension == "paper":
        return ["rolls", "sheets"]
    if dimension == "laundry":
        return ["loads"]
    if dimension == "tissue":
        return ["sheets"]
    if dimension == "oz_labeled":
        return ["oz"]
    return list(_SYSTEM_UNITS.get((dimension, unit_system), []))


def validated_canonical(amount: Decimal, factor: Decimal) -> Decimal:
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be a positive number")
    try:
        canonical = (amount * factor).quantize(_TARGET_QUANTUM, rounding=ROUND_HALF_UP)
    except ArithmeticError as exc:
        raise ValueError("amount out of range") from exc
    if canonical <= 0 or canonical > _TARGET_MAX:
        raise ValueError("amount out of range")
    return canonical


def target_to_canonical(amount: Decimal, unit_code: str, dimension: str) -> tuple[str, Decimal]:
    """(measure key, canonical amount to 3 dp). Raises ValueError for anything invalid."""
    try:
        _label, key, factor = TARGET_UNITS[dimension][unit_code]
    except KeyError as exc:
        raise ValueError(f"unit {unit_code!r} is not valid for {dimension!r}") from exc
    return key, validated_canonical(amount, factor)


def default_target(
    measures: Mapping[str, float], dimension: str, unit_system: str
) -> tuple[Decimal, str] | None:
    """One package of an offer in the viewer's first unit, to 2 dp, or None if unmeasured."""
    if dimension == "paper":
        codes = ["rolls", "sheets"]
    else:
        codes = target_units_for(dimension, unit_system)[:1]
    for code in codes:
        _label, key, factor = TARGET_UNITS[dimension][code]
        raw = measures.get(key)
        if raw is not None:
            amount = (_dec(raw) / factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return amount, code
    return None


def display_target(measure_key: str, amount: Decimal, dimension: str, unit_system: str) -> str:
    return display_size({measure_key: float(amount)}, dimension, unit_system)


_COST_SUFFIX = {"protein": "per 100 g protein", "kcal": "per 1,000 kcal"}


def display_nutrition_cost(cost: Decimal, rank: str, currency: str) -> str:
    """"$1.42 per 100 g protein" or "$0.78 per 1,000 kcal"."""
    return f"{format_money(cost, currency)} {_COST_SUFFIX[rank]}"


def _metric_pack(pack: float, unit: str) -> str:
    value = _dec(pack)
    big, small = ("kg", "g") if unit == "g" else ("L", "ml")
    return f"{_num(value / 1000)} {big}" if value >= 1000 else f"{_num(value)} {small}"


def display_nutrition_used(nutrition: Mapping, rank: str) -> str:
    """The OFF values behind a cost: "21 g protein per 100 g · 907 g pack". Always metric."""
    per = "100 g" if nutrition["basis"] == "100g" else "100 ml"
    pack = _metric_pack(nutrition["pack"], nutrition["pack_unit"])
    if rank == "protein":
        value = f"{_num(_dec(nutrition['protein']))} g protein"
    else:
        value = f"{_num(_dec(round(nutrition['kcal'])))} kcal"
    return f"{value} per {per} · {pack} pack"
