"""Whole-package quantity fit. Pure logic, no Django."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MAX_PACKAGES = 24
FIT_TOLERANCE = Decimal("0.000001")  # relative

# Generic offers carry their size in measure_amount when measure_dim names the key.
GENERIC_MEASURE_KEYS = frozenset({"volume", "mass", "oz_labeled", "count"})


def packages_needed(target: Decimal, size: Decimal) -> int | None:
    """Smallest whole n >= 1 with n * size >= target * (1 - FIT_TOLERANCE).

    The tolerance absorbs float noise in stored sizes (an exact multiple must
    not round up to one more package). None for a non-positive size or when
    more than MAX_PACKAGES would be needed.
    """
    if size <= 0:
        return None
    floor = target * (1 - FIT_TOLERANCE)
    n = max(1, int(floor // size))
    while n * size < floor:
        n += 1
    return n if n <= MAX_PACKAGES else None


@dataclass(frozen=True)
class Fit:
    offer: Any
    packages: int
    package_size: Decimal
    total_price: Decimal
    total_amount: Decimal
    surplus: Decimal


def _package_size(offer: Any, measure_key: str) -> Decimal | None:
    if (
        measure_key in GENERIC_MEASURE_KEYS
        and getattr(offer, "measure_dim", None) == measure_key
        and getattr(offer, "measure_amount", None) is not None
    ):
        return Decimal(str(offer.measure_amount))
    value = (offer.measures or {}).get(measure_key)
    return None if value is None else Decimal(str(value))


def fit_offer(offer: Any, measure_key: str, target: Decimal) -> Fit | None:
    size = _package_size(offer, measure_key)
    if size is None:
        return None
    n = packages_needed(target, size)
    if n is None:
        return None
    total_amount = size * n
    return Fit(
        offer=offer,
        packages=n,
        package_size=size,
        total_price=offer.price * n,
        total_amount=total_amount,
        surplus=max(Decimal(0), total_amount - target),
    )


def fit_key(fit: Fit) -> tuple:
    o = fit.offer
    return (
        fit.total_price,
        fit.surplus,
        fit.packages,
        o.title,
        o.retailer,
        o.retailer_item_id,
    )
