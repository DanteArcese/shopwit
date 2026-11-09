"""Per-store totals and the cheapest single store / split. Pure logic, no Django."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

# The split search tries every store subset up to the size limit, so it is capped:
# with more stores than this, only the MAX_SPLIT_STORES stores that cover the most
# items (cheaper covered cost, then code, on ties) are searched. That keeps the
# worst case at 2**12 subsets and the choice still depends only on prices and stores.
MAX_SPLIT_STORES = 12


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
    if not totals or not any(t.covered or t.missing for t in totals):
        return None
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
    if not costs:
        return None
    stores = _split_candidates(costs, stores)
    # Lower bound on the total: every item at its overall cheapest allowed store.
    bound = _assign(costs, set(stores))
    if bound is None:
        return None
    limit = len(stores) if max_stores is None else min(max_stores, len(stores))
    best: Split | None = None
    for k in range(1, limit + 1):
        for subset in itertools.combinations(sorted(stores), k):
            cand = _assign(costs, set(subset))
            if cand is None:
                continue
            if best is None or _rank(cand) < _rank(best):
                best = cand
        if best is not None and best.total == bound.total:
            break  # larger subsets cannot beat this total with fewer stores
    return best


def _split_candidates(costs, stores: Sequence[str]) -> list[str]:
    stores = sorted(set(stores))
    if len(stores) <= MAX_SPLIT_STORES:
        return stores

    def key(store):
        covered = [c[store] for c in costs.values() if store in c]
        return (-len(covered), sum(covered, Decimal(0)), store)

    return sorted(stores, key=key)[:MAX_SPLIT_STORES]


def _rank(s: Split) -> tuple:
    return (s.total, len(s.stores), s.stores)
