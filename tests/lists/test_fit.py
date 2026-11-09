from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from lists.fit import (
    FIT_TOLERANCE,
    MAX_PACKAGES,
    Fit,
    fit_key,
    fit_offer,
    packages_needed,
)

NOW = datetime(2026, 11, 8, tzinfo=UTC)


@dataclass
class FakeOffer:
    price: Decimal
    measures: dict = field(default_factory=dict)
    currency: str = "USD"
    retailer: str = "x"
    title: str = "milk"
    retailer_item_id: str = "1"
    last_seen_at: datetime = NOW
    measure_dim: str | None = None
    measure_amount: float | None = None


def test_packages_never_fractional():
    for target in range(1, 501):
        for size in (1, 3, 7.5, 64, 473.176):
            t, s = Decimal(target), Decimal(str(size))
            n = packages_needed(t, s)
            if n is None:
                assert (t * (1 - FIT_TOLERANCE)) / s > MAX_PACKAGES
                continue
            floor = t * (1 - FIT_TOLERANCE)
            assert isinstance(n, int) and n >= 1
            assert n * s >= floor
            assert (n - 1) * s < floor


def test_exact_multiple_not_rounded_up():
    assert packages_needed(Decimal("3785.412"), Decimal(str(1892.70592))) == 2
    assert packages_needed(Decimal("3785.412"), Decimal(1890)) == 3
    assert packages_needed(Decimal("12.000"), Decimal(12)) == 1
    # 1 gal of 1.89 L packages
    assert packages_needed(Decimal("3785.412"), Decimal(1890)) == 3
    # float noise from the stored size on either side of exact
    assert packages_needed(Decimal(128), Decimal(str(64.00000000000001))) == 2
    assert packages_needed(Decimal(128), Decimal(str(63.99999999999999))) == 2


def test_too_many_packages_is_none():
    assert packages_needed(Decimal(25), Decimal(1)) is None
    assert packages_needed(Decimal(24), Decimal(1)) == 24
    assert packages_needed(Decimal(25), Decimal(0)) is None
    assert packages_needed(Decimal(25), Decimal(-1)) is None


def test_fit_prefers_one_128_over_two_64():
    small = FakeOffer(Decimal("3.00"), {"volume_floz": 64.0}, title="small")
    big = FakeOffer(Decimal("5.48"), {"volume_floz": 128.0}, title="big")
    fits = [fit_offer(o, "volume_floz", Decimal(128)) for o in (small, big)]
    best = min(fits, key=fit_key)
    assert (
        best.offer is big and best.packages == 1 and best.total_price == Decimal("5.48")
    )
    other = fits[0]
    assert other.packages == 2 and other.total_price == Decimal("6.00")
    assert isinstance(best, Fit)


def test_fit_tie_prefers_less_surplus():
    c = FakeOffer(Decimal("4.00"), {"m": 20.0}, title="c")  # 1 pkg, 4.00, surplus 6
    d = FakeOffer(Decimal("2.00"), {"m": 7.0}, title="d")  # 2 pkgs, 4.00, surplus 0
    best = min(
        (fit_offer(c, "m", Decimal(14)), fit_offer(d, "m", Decimal(14))), key=fit_key
    )
    assert best.offer is d and best.surplus == 0


def test_fit_missing_measure_is_none():
    assert (
        fit_offer(FakeOffer(Decimal(1), {"mass_g": 5.0}), "volume_floz", Decimal(3))
        is None
    )
    assert fit_offer(FakeOffer(Decimal(1), {"m": 1.0}), "m", Decimal(100)) is None


def test_fit_amounts():
    f = fit_offer(FakeOffer(Decimal("1.50"), {"m": 10.0}), "m", Decimal(25))
    assert f.packages == 3 and f.package_size == Decimal("10.0")
    assert f.total_amount == Decimal("30.0") and f.surplus == Decimal("5.0")
    assert f.total_price == Decimal("4.50")


def test_fit_generic_measure_amount():
    o = FakeOffer(Decimal("2.00"), {}, measure_dim="volume", measure_amount=473.176)
    f = fit_offer(o, "volume", Decimal(946))
    assert f.packages == 2 and f.package_size == Decimal("473.176")
    # dimension mismatch: not read from measure_amount, and measures lacks it
    assert fit_offer(o, "mass", Decimal(10)) is None
    # non-generic key still reads measures
    o2 = FakeOffer(
        Decimal("2.00"), {"volume": 5.0}, measure_dim="mass", measure_amount=9.0
    )
    assert fit_offer(o2, "volume", Decimal(5)).package_size == Decimal("5.0")
    # dim matches but amount missing -> falls back to measures, then None
    o3 = FakeOffer(Decimal("2.00"), {}, measure_dim="count", measure_amount=None)
    assert fit_offer(o3, "count", Decimal(1)) is None


def test_packages_needed_guards():
    assert packages_needed(Decimal(5), Decimal("1e-30")) is None
    assert packages_needed(Decimal("1e30"), Decimal("1e-30")) is None
    assert packages_needed(Decimal(0), Decimal(5)) is None
    assert packages_needed(Decimal(-3), Decimal(5)) is None
