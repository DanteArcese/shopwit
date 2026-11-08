import itertools
import random
from decimal import Decimal

from lists.basket import cheapest_single_store, cheapest_split, store_totals

D = Decimal


def test_store_totals_and_missing():
    costs = {"a": {"x": D(2), "y": D(3)}, "b": {"x": D(4)}}
    tx, ty = store_totals(costs, ["x", "y"])
    assert tx.total == 6 and tx.covered == ("a", "b") and tx.missing == ()
    assert ty.total == 3 and ty.covered == ("a",) and ty.missing == ("b",)


def test_single_store_requires_full_coverage():
    costs = {"a": {"x": D(5), "y": D(1)}, "b": {"x": D(5)}}
    best = cheapest_single_store(store_totals(costs, ["x", "y"]))
    assert best.store == "x" and best.total == 10


def test_single_store_none_when_no_store_covers():
    costs = {"a": {"x": D(1)}, "b": {"y": D(1)}}
    assert cheapest_single_store(store_totals(costs, ["x", "y"])) is None


def test_single_store_tie_by_store_code():
    costs = {"a": {"x": D(1), "y": D(1)}}
    assert cheapest_single_store(store_totals(costs, ["y", "x"])).store == "x"


def test_split_unlimited_is_per_item_min():
    costs = {"a": {"x": D(1), "y": D(5)}, "b": {"x": D(9), "y": D(2)}}
    s = cheapest_split(costs, ["x", "y"], None)
    assert s.total == 3 and s.assignments == {"a": "x", "b": "y"}
    assert s.stores == ("x", "y")


def test_split_max_stores_two():
    costs = {
        "a": {"x": D(1), "y": D(6), "z": D(7)},
        "b": {"x": D(8), "y": D(2), "z": D(9)},
        "c": {"x": D(9), "y": D(8), "z": D(3)},
    }
    s = cheapest_split(costs, ["x", "y", "z"], 2)
    # {x,y}: 1+2+8=11; {x,z}: 1+8+3=12; {y,z}: 6+2+3=11 -> tie, sorted tuple x,y wins
    assert s.total == 11 and s.stores == ("x", "y")
    assert len(s.stores) == 2


def test_split_none_when_uncoverable():
    assert cheapest_split({"a": {"x": D(1)}, "b": {"y": D(1)}}, ["x", "y"], 1) is None
    assert cheapest_split({"a": {}}, ["x"], None) is None


def test_split_tie_prefers_fewer_stores():
    costs = {"a": {"x": D(1), "y": D(1)}, "b": {"x": D(1), "y": D(1)}}
    s = cheapest_split(costs, ["x", "y"], 2)
    assert s.stores == ("x",) and s.total == 2


def test_split_matches_brute_force():
    rng = random.Random(4)
    names = ["s1", "s2", "s3", "s4"]
    for _ in range(200):
        n_items, n_st = rng.randint(1, 5), rng.randint(1, 4)
        stores = names[:n_st]
        costs = {
            f"i{i}": {s: D(rng.randint(1, 20)) for s in stores if rng.random() < 0.7}
            for i in range(n_items)
        }
        for k in (1, 2, None):
            got = cheapest_split(costs, stores, k)
            items = list(costs)
            best = None
            for combo in itertools.product(stores, repeat=len(items)):
                if len(set(combo)) > (k or 99):
                    continue
                if any(s not in costs[i] for i, s in zip(items, combo)):
                    continue
                t = sum(costs[i][s] for i, s in zip(items, combo))
                best = t if best is None or t < best else best
            if best is None:
                assert got is None
            else:
                assert got is not None and got.total == best
                assert len(set(got.assignments.values())) <= (k or 99)
