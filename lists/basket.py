"""Per-store totals and the cheapest single store / split. Pure logic, no Django."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StoreTotal:
    store: str
    total: Decimal
    covered: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class Split:
    total: Decimal
    stores: tuple[str, ...]
    assignments: Mapping[str, str]


def store_totals(
    costs: Mapping[str, Mapping[str, Decimal]], stores: Sequence[str]
) -> list[StoreTotal]:
    out = []
    for store in stores:
        covered = tuple(i for i in costs if store in costs[i])
        missing = tuple(i for i in costs if store not in costs[i])
        total = sum((costs[i][store] for i in covered), Decimal(0))
        out.append(StoreTotal(store, total, covered, missing))
    return out


def cheapest_single_store(totals: Sequence[StoreTotal]) -> StoreTotal | None:
    full = [t for t in totals if not t.missing]
    return min(full, key=lambda t: (t.total, t.store)) if full else None


def _assign(costs, allowed) -> Split | None:
    assignments = {}
    total = Decimal(0)
    for item, by_store in costs.items():
        options = [(c, s) for s, c in by_store.items() if s in allowed]
        if not options:
            return None
        cost, store = min(options)
        assignments[item] = store
        total += cost
    return Split(total, tuple(sorted(set(assignments.values()))), assignments)


def cheapest_split(
    costs: Mapping[str, Mapping[str, Decimal]],
    stores: Sequence[str],
    max_stores: int | None,
) -> Split | None:
    if max_stores is None or max_stores >= len(stores):
        return _assign(costs, set(stores))
    best: Split | None = None
    for k in range(1, max_stores + 1):
        for subset in itertools.combinations(sorted(stores), k):
            cand = _assign(costs, set(subset))
            if cand is None:
                continue
            if best is None or _rank(cand) < _rank(best):
                best = cand
    return best


def _rank(s: Split) -> tuple:
    return (s.total, len(s.stores), s.stores)
